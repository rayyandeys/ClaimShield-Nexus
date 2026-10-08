"""Strict snapshot ingestion. Any invalid row rejects the transaction, retaining a report."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from pydantic import BaseModel, Field, ValidationError
from app.core import engine, settings, now, clean
from app.models import SPECS, NULLABLE, INTS, DATES, FK, tables, batches, entities

class FinancialRow(BaseModel):
    billed_amount_usd: Decimal = Field(ge=0)
    allowed_amount_usd: Decimal = Field(ge=0)
    paid_amount_usd: Decimal = Field(ge=0)
    member_responsibility_usd: Decimal = Field(ge=0)

def audit_directory(directory: Path):
    data, issues, sources = {}, [], {}
    def issue(table, row, field, message, severity='ERROR'):
        issues.append(dict(table=table, record_id=row, field=field, message=message, severity=severity))
    for name, spec in SPECS.items():
        path = directory / f'{name}.csv'
        if not path.exists():
            issue(name, '', '', 'Required reference file is missing'); data[name] = []; continue
        sources[name] = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        with path.open(newline='', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            required = spec.split()
            if reader.fieldnames != required:
                issue(name, '', '', f'Expected exact header: {spec}'); data[name] = []; continue
            rows = []
            seen = set()
            for line_no, raw in enumerate(reader, 2):
                row = {}
                key = raw.get(required[0], '')
                if key in seen: issue(name, key, required[0], 'Duplicate primary key')
                seen.add(key)
                for col in required:
                    v = raw.get(col)
                    try:
                        if v is None: raise ValueError('Malformed CSV row')
                        v = v.strip()
                        if not v:
                            if col not in NULLABLE or col == required[0]: raise ValueError('Required value is missing')
                            row[col] = None; continue
                        if col in INTS:
                            v = int(v)
                            if v < 0: raise ValueError('Negative quantity or duration')
                        elif col.endswith('_usd'):
                            v = Decimal(v)
                            if not v.is_finite() or (col != 'difference_usd' and v < 0): raise ValueError('Invalid financial amount')
                        elif col in DATES: v = date.fromisoformat(v)
                        elif col == 'service_start': v = datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
                        row[col] = v
                    except (ValueError, InvalidOperation) as e:
                        issue(name, key or f'line {line_no}', col, str(e)); row[col] = None
                rows.append(row)
            data[name] = rows
    if any(i['severity'] == 'ERROR' for i in issues):
        return data, {'status': 'REJECTED', 'issues': issues, 'sources': sources, 'counts': {k: len(v) for k,v in data.items()}}
    lookup = {name: {r[spec.split()[0]]: r for r in data[name]} for name, spec in SPECS.items()}
    for name, rows in data.items():
        pk = SPECS[name].split()[0]
        for row in rows:
            for col, reference in FK.items():
                if col not in row or col == pk or row[col] is None: continue
                target = reference.split('.')[0]
                if row[col] not in lookup[target]: issue(name, row[pk], col, f'Broken reference to {target}')
    entity_types = {'provider': set(lookup['providers']), 'facility': set(lookup['facilities']), 'member': set(lookup['members']), 'claim': set(lookup['claims']), 'owner': {r['owner_id'] for r in data['facilities']}}
    for r in data['relationships']:
        for side in ['source', 'target']:
            if r[f'{side}_id'] not in entity_types.get(r[f'{side}_type'], set()): issue('relationships', r['relationship_id'], side, 'Unknown typed endpoint')
        if r['effective_to'] and r['effective_to'] < r['effective_from']: issue('relationships', r['relationship_id'], 'effective_to', 'Ends before start')
    line_totals = defaultdict(Decimal)
    for r in data['claim_lines']:
        line_totals[r['claim_id']] += r['line_billed_usd']
        if abs(r['unit_count'] * r['unit_charge_usd'] - r['line_billed_usd']) > Decimal('1.00'):
            issue('claim_lines', r['claim_line_id'], 'line_billed_usd', 'Quantity × rounded unit price differs by more than $1', 'WARNING')
    for r in data['claims']:
        key = r['claim_id']
        FinancialRow.model_validate(r)
        if r['service_date'] != r['service_start'].date(): issue('claims', key, 'service_start', 'Service date mismatch')
        if r['submitted_date'] < r['service_date']: issue('claims', key, 'submitted_date', 'Submission precedes service')
        if r['payment_date'] and r['payment_date'] < r['submitted_date']: issue('claims', key, 'payment_date', 'Payment precedes submission')
        if r['allowed_amount_usd'] > r['billed_amount_usd']: issue('claims', key, 'allowed_amount_usd', 'Allowed exceeds billed')
        if r['paid_amount_usd'] + r['member_responsibility_usd'] > r['allowed_amount_usd'] + Decimal('.02'): issue('claims', key, 'paid_amount_usd', 'Paid plus responsibility exceeds allowed')
        if abs(line_totals[key] - r['billed_amount_usd']) > Decimal('.02'): issue('claims', key, 'billed_amount_usd', 'Claim total differs from lines')
        e = lookup['encounters'].get(r['encounter_id'])
        if e:
            for col in ['provider_id', 'member_id', 'facility_id']:
                if r[col] != e[col]: issue('claims', key, col, 'Encounter context conflicts; retained for review', 'WARNING')
        else: issue('claims', key, 'encounter_id', 'No encounter reference; documentation gap', 'WARNING')
        if r['correction_of_claim_id'] == key: issue('claims', key, 'correction_of_claim_id', 'Self correction reference')
    for r in data['supply_items']:
        line = lookup['claim_lines'].get(r['claim_line_id'])
        if line and line['claim_id'] != r['claim_id']: issue('supply_items', r['supply_id'], 'claim_line_id', 'Supply and line belong to different claims')
        if line and abs(line['line_billed_usd'] - r['line_billed_usd']) > Decimal('.02'): issue('supply_items', r['supply_id'], 'line_billed_usd', 'Supply differs from linked line')
    for r in data['claim_estimates']:
        if abs(r['final_billed_usd'] - r['estimated_billed_usd'] - r['difference_usd']) > Decimal('.02'): issue('claim_estimates', r['estimate_id'], 'difference_usd', 'Estimate arithmetic mismatch')
        c = lookup['claims'].get(r['claim_id'])
        if c and r['final_billed_usd'] != c['billed_amount_usd']: issue('claim_estimates', r['estimate_id'], 'final_billed_usd', 'Estimate final differs from claim')
    for r in data['investigation_history']:
        if r['closed_date'] and r['closed_date'] < r['opened_date']: issue('investigation_history', r['investigation_id'], 'closed_date', 'Closure precedes opening')
        if r['outcome_available_date'] and r['closed_date'] and r['outcome_available_date'] < r['closed_date']: issue('investigation_history', r['investigation_id'], 'outcome_available_date', 'Outcome available before closure')
    report = {'status': 'REJECTED' if any(i['severity']=='ERROR' for i in issues) else 'PASS_WITH_WARNINGS' if issues else 'PASS', 'issues': issues, 'sources': sources, 'counts': {k:len(v) for k,v in data.items()}, 'date_range': [str(min(r['service_date'] for r in data['claims'])), str(max(r['service_date'] for r in data['claims']))] if data['claims'] else [], 'assumptions': ['Naive synthetic timestamps interpreted as UTC.', 'Rounded unit-price arithmetic warnings preserve source values.', 'Potential exposure is not confirmed recoverable overpayment.', 'Ground truth is excluded from operational ingestion.'], 'synthetic': True}
    return data, report

def import_dataset(directory=None, progress=lambda v: None):
    directory = Path(directory or settings.data_dir / 'synthetic')
    batch_id = 'IMP-' + uuid4().hex[:16]
    with engine.begin() as con:
        con.execute(batches.insert().values(batch_id=batch_id, kind='import', status='VALIDATING', source=str(directory), progress=0))
    data, report = audit_directory(directory)
    checksum = hashlib.sha256(json.dumps(report['sources'], sort_keys=True).encode()).hexdigest()
    progress(20)
    try:
        if report['status'] == 'REJECTED': raise ValueError('Dataset rejected; inspect the validation issues.')
        with engine.begin() as con:
            # Serialize imports so conflict checks and insertion remain atomic.
            con.exec_driver_sql('SELECT pg_advisory_xact_lock(8231701)')
            previous = con.execute(select(batches.c.batch_id).where(batches.c.checksum == checksum, batches.c.status == 'COMPLETED')).scalar()
            if previous:
                report['reused_batch_id'] = previous
                report['inserted_counts'] = {k: 0 for k in data}
            else:
                entity_rows = []
                for name, typ, label in [('members','member','member_id'),('facilities','facility','facility_name'),('providers','provider','provider_name'),('claims','claim','claim_id')]:
                    pk = SPECS[name].split()[0]
                    entity_rows.extend(dict(entity_id=r[pk], entity_type=typ, label=r[label], source_table=name) for r in data[name])
                entity_rows.extend(dict(entity_id=k, entity_type='owner', label=f'Synthetic owner {k}', source_table='facilities') for k in sorted({r['owner_id'] for r in data['facilities']}))
                for start in range(0,len(entity_rows),1000): con.execute(insert(entities).on_conflict_do_nothing(), entity_rows[start:start+1000])
                report['inserted_counts'] = {}
                for n,(name,rows) in enumerate(data.items()):
                    table = tables[name]; pk = SPECS[name].split()[0]
                    existing = {r[pk]:r for r in con.execute(select(table)).mappings()}
                    fresh = []
                    for r in rows:
                        if r[pk] in existing:
                            if any(existing[r[pk]][k] != v for k,v in r.items()): raise ValueError(f'Conflicting existing source ID {name}/{r[pk]}; source records are immutable.')
                        else: fresh.append(dict(r, source_batch_id=batch_id, source_file=f'{name}.csv'))
                    for start in range(0,len(fresh),1000): con.execute(table.insert(), fresh[start:start+1000])
                    report['inserted_counts'][name] = len(fresh)
                    progress(20 + 75*(n+1)/len(data))
            con.execute(update(batches).where(batches.c.batch_id == batch_id).values(status='COMPLETED', progress=100, report=clean(report), checksum=checksum, completed_at=now()))
    except Exception as exc:
        report['failure'] = str(exc)[:1000]
        with engine.begin() as con: con.execute(update(batches).where(batches.c.batch_id==batch_id).values(status='REJECTED', report=clean(report), completed_at=now()))
        raise ValueError(f'Import {batch_id} rejected: {report["failure"]}') from exc
    settings.artifact_dir.joinpath('evaluation').mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.joinpath('evaluation/latest_audit.json').write_text(json.dumps(clean(report), indent=2))
    return {'batch_id':batch_id, **report}
