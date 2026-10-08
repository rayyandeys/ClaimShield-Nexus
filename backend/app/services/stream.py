"""Live claims monitoring: durable stream sessions, immediate screening (Layer A), micro-batch enrichment (Layer B),
per-claim stage coverage and ordered processing events.

Layer A (stream runner process, per claim): validation, persistence, Claim Rules, SupplyTrace, finding persistence,
incremental case linking and SIU recalculation. Layer B (existing analytics worker, job kind `stream_enrichment`):
Isolation Forest and forecast inference with the stored models, Nexus Graph neighborhood and referral rule, Member Radar
and Phoenix, scoped to the providers and members of the covered claims, followed by final evidence fusion, case
reconciliation and ranking. A claim is FULLY_ANALYZED only when all twelve stages are COMPLETED, NOT_APPLICABLE or
INSUFFICIENT_DATA (a documented terminal no-prediction outcome).

Every processing event is written in the same transaction as the state it describes (outbox); nothing is published
before commit.
"""
import logging
import random
import socket
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select, update, delete, func, and_, or_, case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from app.core import engine, settings, clean, now
from app import models as m
from app.services import stream_generator as gen
from app.services.cases import ACTIVE as ACTIVE_FINDINGS, link_incremental, recalculate, queue_position
from app.services.detection import rules, supply_analysis, utilization_observations, utilization_thresholds
from app.services.ingestion import claim_issues, supply_issues, estimate_issues

log = logging.getLogger(__name__)
PROCESSING_VERSION = 'live-stream-1.0'
STAGES = [('validation', 'Validation', 'immediate'), ('persistence', 'Persistence', 'immediate'), ('claim_rules', 'Claim Rules', 'immediate'), ('supplytrace', 'SupplyTrace', 'immediate'),
          ('isolation_forest', 'Isolation Forest', 'enrichment'), ('nexus_graph', 'Nexus Graph', 'enrichment'), ('member_radar', 'Member Radar', 'enrichment'), ('phoenix', 'Phoenix', 'enrichment'), ('forecast', 'Forecast inference', 'enrichment'),
          ('evidence_fusion', 'Evidence fusion', 'final'), ('case_consolidation', 'Case consolidation', 'final'), ('siu_ranking', 'SIU ranking', 'final')]
STAGE_NAMES = [s for s, _, _ in STAGES]
ENRICHMENT_STAGES = [s for s, _, layer in STAGES if layer != 'immediate']
TERMINAL_OK = {'COMPLETED', 'NOT_APPLICABLE', 'INSUFFICIENT_DATA'}
MAX_ATTEMPTS = 3
LOCK_CASES, LOCK_EVENTS, LOCK_SESSION = 8231702, 8231710, 8231711
AWAITING_LAYER_A = ('ACCEPTED', 'RETRYING', 'PROCESSING')
AWAITING_ENRICHMENT = ('IMMEDIATE_DONE', 'ENRICH_RETRY', 'ENRICHING')
TERMINAL_CLAIM = ('FULLY_ANALYZED', 'FAILED')

class Conflict(Exception):
    """Request conflicts with the current recorded stream state (HTTP 409)."""

class Unavailable(Exception):
    """The stream runner process is not running (HTTP 503)."""

# ---------------------------------------------------------------- events (outbox)
def event(session_id, kind, message, **refs):
    payload = refs.pop('payload', {})
    return dict(session_id=session_id, event_type=kind, message=message, claim_id=refs.get('claim_id'), finding_id=refs.get('finding_id'), case_id=refs.get('case_id'), provider_id=refs.get('provider_id'), stage=refs.get('stage'), payload=clean(payload))

def emit(con, events):
    """Inserted as the last statement of the transaction that made the change, under an advisory lock held until commit,
    so event IDs become visible in commit order and a cursor reader can never skip an event."""
    if not events: return
    con.exec_driver_sql(f'SELECT pg_advisory_xact_lock({LOCK_EVENTS})')
    con.execute(m.stream_events.insert(), events)

def events_after(con, session_id=None, after=0, limit=200):
    q = select(m.stream_events).where(m.stream_events.c.event_id > after)
    if session_id: q = q.where(m.stream_events.c.session_id == session_id)
    return [clean(dict(r)) for r in con.execute(q.order_by(m.stream_events.c.event_id).limit(limit)).mappings()]

# ---------------------------------------------------------------- sessions
def runner_alive(con, within=20):
    return bool(con.execute(select(func.count()).select_from(m.stream_runners).where(m.stream_runners.c.heartbeat_at > now() - timedelta(seconds=within))).scalar())

def heartbeat(runner_id):
    with engine.begin() as con:
        stmt = insert(m.stream_runners).values(runner_id=runner_id, heartbeat_at=now(), host=socket.gethostname())
        con.execute(stmt.on_conflict_do_update(index_elements=['runner_id'], set_={'heartbeat_at': now()}))
        con.execute(update(m.stream_sessions).where(m.stream_sessions.c.runner_id == runner_id, m.stream_sessions.c.status.in_(m.STREAM_ACTIVE)).values(lease_until=now() + timedelta(seconds=30)))

def start_session(rate=None, count=None, seed=None, mode='mixed_demo', require_runner=True):
    rate = settings.stream_default_rate if rate is None else float(rate); count = settings.stream_default_count if count is None else int(count)
    seed = gen.DEFAULT_SEED if seed is None else int(seed)
    gen.validate_config(rate, count, seed, mode, settings.stream_max_count)
    try:
        with engine.begin() as con:
            con.exec_driver_sql(f'SELECT pg_advisory_xact_lock({LOCK_SESSION})')
            active = con.execute(select(m.stream_sessions.c.session_id).where(m.stream_sessions.c.status.in_(m.STREAM_ACTIVE))).scalar()
            if active: raise Conflict(f'Stream session {active} is already active; stop it or wait for it to drain.')
            if require_runner and not runner_alive(con): raise Unavailable('The stream runner service is not running (docker compose up -d stream).')
            number = (con.execute(select(func.max(m.stream_sessions.c.session_number))).scalar() or 0) + 1
            sid = f'STREAM-{number:04d}'; batch_id = f'STR-{number:04d}-{uuid4().hex[:8]}'
            config = {'rate_per_second': rate, 'max_claims': count, 'seed': seed, 'scenario_mode': mode, 'scenario_description': gen.MODES[mode], 'enrichment_interval_seconds': settings.stream_enrichment_interval_seconds, 'max_backlog': settings.stream_max_backlog, 'pack_version': gen.PACK_VERSION, 'processing_version': PROCESSING_VERSION}
            con.execute(m.batches.insert().values(batch_id=batch_id, kind='stream', status='RUNNING', source=f'Live stream session {sid} ({gen.PACK_VERSION}, seed {seed}, synthetic)', progress=0))
            con.execute(m.stream_sessions.insert().values(session_id=sid, session_number=number, status='STARTING', config=config, seed=seed, scenario_version=gen.PACK_VERSION, intended_count=count, generated_count=0, rejected_count=0, max_backlog=0, stop_requested=False, cohort={}, batch_id=batch_id, error={}))
            emit(con, [event(sid, 'session_created', f'Session {sid} created: {count} claims at {rate:g}/s, seed {seed}, {mode.replace("_", " ")}.', payload=config)])
    except IntegrityError as exc:
        raise Conflict('Another stream session became active at the same time; only one session can run.') from exc
    return status(sid)

def stop_session(session_id):
    with engine.begin() as con:
        s = con.execute(select(m.stream_sessions).where(m.stream_sessions.c.session_id == session_id).with_for_update()).mappings().first()
        if not s: raise LookupError('Stream session not found')
        if s['status'] not in m.STREAM_ACTIVE: return {'session_id': session_id, 'status': s['status'], 'acknowledged': False, 'reason': 'Session is not active.'}
        if s['stop_requested']: return {'session_id': session_id, 'status': s['status'], 'acknowledged': True, 'generated_count': s['generated_count']}
        new = 'STOPPING' if s['status'] in ('RUNNING', 'STARTING') else s['status']
        con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == session_id).values(stop_requested=True, status=new, stop_requested_at=now(), updated_at=now()))
        emit(con, [event(session_id, 'stop_requested', f'Stop acknowledged after {s["generated_count"]} generated claims; no further claims will be generated. Accepted claims will finish processing.', payload={'generated_count': s['generated_count']})])
        return {'session_id': session_id, 'status': new, 'acknowledged': True, 'generated_count': s['generated_count']}

def context_pools(con, description_roles, session_seed, need):
    """Existing synthetic members with a prior claim for one of the role's codes (genuine care context). Excluded: members
    touched by any provider outside the original snapshot (scenario pack, earlier stream sessions), members of qualified
    Member Radar batches and members with simulated confirmations, so scenario fixtures and reviews stay untouched and
    sessions never reuse a member."""
    claims, providers = m.tables['claims'], m.tables['providers']
    original_batch = con.execute(select(providers.c.source_batch_id).order_by(providers.c.created_at, providers.c.provider_id).limit(1)).scalar()
    touched = select(claims.c.member_id).join(providers, providers.c.provider_id == claims.c.provider_id).where(providers.c.source_batch_id != original_batch)
    pools = {}
    for role in ['A', 'B']:
        _, ctype, _, codes, *_ = gen.ROLES[role]
        flagged = select(m.radar_batch_members.c.member_id).join(m.radar_batches, m.radar_batches.c.provider_id == m.radar_batch_members.c.provider_id).where(m.radar_batches.c.qualified.is_(True))
        rows = con.execute(select(claims.c.member_id, func.max(claims.c.primary_code)).where(claims.c.claim_type == ctype, claims.c.primary_code.in_([c for c, _ in codes]), claims.c.service_date < gen.FIRST_DAY, ~claims.c.member_id.in_(touched), ~claims.c.member_id.in_(flagged), ~claims.c.member_id.in_(select(m.confirmations.c.member_id))).group_by(claims.c.member_id).order_by(claims.c.member_id)).all()
        pool = [(r[0], r[1]) for r in rows]; random.Random(f'{session_seed}:pool:{role}').shuffle(pool); pools[role] = pool
    # A member is used by at most one role; overlap is removed before truncating so each role still gets `need` members.
    taken = set(); out = {}
    for role in ['A', 'B']:
        out[role] = [p for p in pools[role] if p[0] not in taken][:need[role]]; taken.update(p[0] for p in out[role])
    return out

def activate(session, runner_id):
    """STARTING -> RUNNING: prepares the session's provider/member cohort (append-only) in one transaction."""
    sid = session['session_id']
    with engine.begin() as con:
        s = con.execute(select(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).with_for_update()).mappings().first()
        if s['status'] != 'STARTING' or s['runner_id'] != runner_id: return None
        cfg = s['config']; need = gen.roles_needed(s['intended_count'], cfg['scenario_mode'])
        pools = context_pools(con, gen.ROLES, s['seed'], need)
        rows, description = gen.cohort(s['session_number'], s['seed'], s['intended_count'], cfg['scenario_mode'], pools)
        for table in ['providers', 'members']:
            if rows[table]: con.execute(insert(m.tables[table]).on_conflict_do_nothing(), [dict(r, source_batch_id=s['batch_id'], source_file='live_stream') for r in rows[table]])
        if rows['entities']: con.execute(insert(m.entities).on_conflict_do_nothing(), rows['entities'])
        for table in ['relationships', 'provider_profiles']:
            con.execute(insert(m.tables[table]).on_conflict_do_nothing(), [dict(r, source_batch_id=s['batch_id'], source_file='live_stream') for r in rows[table]])
        con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).values(status='RUNNING', started_at=now(), cohort=clean(description), updated_at=now()))
        emit(con, [event(sid, 'session_started', f'Session {sid} running. Cohort: providers {", ".join(description["providers"].values())}, {len(description["new_members"])} new synthetic members, {sum(len(v) for v in description["context_members"].values())} existing members with care history.', payload=description)]
             + [event(sid, 'cohort_provider', f'Synthetic stream provider {pid} ({gen.ROLES[role][0]}) enrolled {gen.COHORT_START}; Phoenix and Member Radar evaluate it during enrichment.', provider_id=pid) for role, pid in description['providers'].items()])
    return description

def fail_session(session_id, reason):
    """A non-recoverable session error (e.g. the cohort cannot be prepared): FAILED with the reason, never a silent hang."""
    with engine.begin() as con:
        s = con.execute(select(m.stream_sessions).where(m.stream_sessions.c.session_id == session_id).with_for_update()).mappings().first()
        if not s or s['status'] not in m.STREAM_ACTIVE: return
        con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == session_id).values(status='FAILED', stopped_at=now(), updated_at=now(), error={'reason': reason[:1000]}))
        con.execute(update(m.batches).where(m.batches.c.batch_id == s['batch_id']).values(status='FAILED', completed_at=now(), report={'error': reason[:300]}))
        emit(con, [event(session_id, 'session_failed', f'{session_id} failed: {reason[:300]}. Claims already accepted (if any) remain stored.', payload={'reason': reason[:1000]})])

def acquire(runner_id):
    """Claims (or renews) ownership of the active session. Ownership is a lease in PostgreSQL, so a restarted runner
    resumes from the persisted generated_count and two runners can never generate for the same session."""
    with engine.begin() as con:
        s = con.execute(select(m.stream_sessions).where(m.stream_sessions.c.status.in_(m.STREAM_ACTIVE)).with_for_update(skip_locked=True)).mappings().first()
        if not s: return None
        if s['runner_id'] not in (None, runner_id) and s['lease_until'] and s['lease_until'] > now(): return None
        if s['runner_id'] != runner_id:
            con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == s['session_id']).values(runner_id=runner_id, lease_until=now() + timedelta(seconds=30)))
            emit(con, [event(s['session_id'], 'runner_attached', f'Stream runner attached to {s["session_id"]} at generated claim {s["generated_count"]}.' + (' Previous runner lease expired; resuming without regenerating accepted claims.' if s['runner_id'] else ''))])
        return dict(s, runner_id=runner_id)

_plans = {}
def plan_for(session):
    key = (session['session_id'], session['seed'], session['intended_count'])
    if key not in _plans: _plans[key] = gen.plan(session['seed'], session['intended_count'], session['config']['scenario_mode'], session['cohort'])
    return _plans[key]

def validate_bundle(con, bundle):
    """Same row checks as snapshot ingestion (claim_issues, supply_issues, estimate_issues) plus reference checks against
    stored records. Returns (errors, warnings)."""
    errors, warnings = [], []
    claim = bundle['claims'][0]; lines = bundle['claim_lines']
    def exists(table, key, value): return value is None or bool(con.execute(select(func.count()).select_from(m.tables[table]).where(m.tables[table].c[key] == value)).scalar())
    for field, table, key in [('member_id', 'members', 'member_id'), ('provider_id', 'providers', 'provider_id'), ('facility_id', 'facilities', 'facility_id'), ('correction_of_claim_id', 'claims', 'claim_id'), ('related_claim_id', 'claims', 'claim_id')]:
        if not exists(table, key, claim[field]): errors.append({'field': field, 'message': f'Broken reference to {table}'})
    if exists('claims', 'claim_id', claim['claim_id']): errors.append({'field': 'claim_id', 'message': 'Duplicate primary key'})
    encounter = next((e for e in bundle['encounters'] if e['encounter_id'] == claim['encounter_id']), None)
    if encounter is None and claim['encounter_id']:
        row = con.execute(select(m.tables['encounters']).where(m.tables['encounters'].c.encounter_id == claim['encounter_id'])).mappings().first()
        encounter = dict(row) if row else None
        if not row: errors.append({'field': 'encounter_id', 'message': 'Broken reference to encounters'})
    for line in lines:
        if line['claim_id'] != claim['claim_id']: errors.append({'field': 'claim_lines', 'message': 'Line belongs to another claim'})
        if line['unit_count'] < 0 or line['line_billed_usd'] < 0: errors.append({'field': 'claim_lines', 'message': 'Negative quantity or amount'})
    try:
        for field, message, severity in claim_issues(claim, sum((l['line_billed_usd'] for l in lines), 0), encounter):
            (errors if severity == 'ERROR' else warnings).append({'field': field, 'message': message})
    except Exception as exc:
        errors.append({'field': 'financials', 'message': str(exc)[:300]})
    line_map = {l['claim_line_id']: l for l in lines}
    for s in bundle['supply_items']:
        if s['claim_line_id'] not in line_map: errors.append({'field': 'supply_items', 'message': 'Supply references an unknown claim line'})
        for field, message, severity in supply_issues(s, line_map.get(s['claim_line_id'])): (errors if severity == 'ERROR' else warnings).append({'field': field, 'message': message})
    for e in bundle['claim_estimates']:
        for field, message, severity in estimate_issues(e, claim): (errors if severity == 'ERROR' else warnings).append({'field': field, 'message': message})
    return errors, warnings

def stage_rows(claim_id, run_status):
    rows = []
    for stage in STAGE_NAMES:
        done = stage in ('validation', 'persistence')
        rows.append(dict(claim_id=claim_id, stage=stage, status='COMPLETED' if done else 'PENDING', attempts=1 if done else 0, started_at=now() if done else None, completed_at=now() if done else None, findings_count=0, result={}, processing_version=PROCESSING_VERSION))
    return rows

def generate_one(session, runner_id, bundle_override=None):
    """Generates, validates and persists the next arrival in one transaction. The conditional counter update (owner,
    RUNNING, expected sequence) makes generation exactly-once across restarts and competing runners."""
    sid = session['session_id']; items = plan_for(session); seq = session['generated_count'] + 1
    if seq > len(items): return None
    item = items[seq - 1]; generated_at = now()
    bundle = bundle_override or gen.bundle(item, items, session['session_number'], session['seed'])
    claim = bundle['claims'][0]
    with engine.begin() as con:
        advanced = con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid, m.stream_sessions.c.runner_id == runner_id, m.stream_sessions.c.status == 'RUNNING', m.stream_sessions.c.generated_count == seq - 1).values(generated_count=seq, updated_at=now())).rowcount
        if not advanced: raise Conflict('Session state changed (stopped, completed or taken over); arrival not generated.')
        errors, warnings = validate_bundle(con, bundle)
        label = gen.SCENARIO_LABELS.get(item['scenario'], item['scenario'])
        generated = event(sid, 'claim_generated', f'#{seq} {claim["claim_id"]} generated · {claim["claim_type"]} {claim["primary_code"]} · ${float(claim["billed_amount_usd"]):,.2f} billed.', claim_id=claim['claim_id'], provider_id=claim['provider_id'], payload={'sequence': seq, 'scenario': item['scenario'], 'scenario_label': label, 'service_date': claim['service_date'], 'generated_at': generated_at})
        if errors:
            con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).values(rejected_count=m.stream_sessions.c.rejected_count + 1))
            emit(con, [generated, event(sid, 'claim_rejected', f'#{seq} {claim["claim_id"]} rejected by validation: {"; ".join(e["field"] + " " + e["message"] for e in errors)}. Nothing was persisted.', claim_id=claim['claim_id'], stage='validation', payload={'errors': errors, 'sequence': seq})])
            return {'sequence': seq, 'claim_id': claim['claim_id'], 'accepted': False, 'errors': errors}
        tag = dict(source_batch_id=session['batch_id'], source_file='live_stream')
        con.execute(m.entities.insert(), [dict(entity_id=claim['claim_id'], entity_type='claim', label=claim['claim_id'], source_table='claims')])
        for table in ['encounters', 'claims', 'claim_lines', 'supply_items', 'claim_estimates']:
            if bundle[table]: con.execute(m.tables[table].insert(), [dict(r, **tag) for r in bundle[table]])
        con.execute(m.stream_claims.insert().values(claim_id=claim['claim_id'], session_id=sid, sequence=seq, scenario=item['scenario'], generated_at=generated_at, ingested_at=func.clock_timestamp(), service_date=claim['service_date'], analysis_status='ACCEPTED', current_stage='claim_rules', attempts=0, finding_ids=[], case_ids=[]))
        con.execute(m.stream_stages.insert(), stage_rows(claim['claim_id'], 'ACCEPTED'))
        backlog = con.execute(select(func.count()).select_from(m.stream_claims).where(m.stream_claims.c.session_id == sid, ~m.stream_claims.c.analysis_status.in_(TERMINAL_CLAIM))).scalar()
        con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).values(max_backlog=func.greatest(m.stream_sessions.c.max_backlog, backlog)))
        refs = dict(claim_id=claim['claim_id'], provider_id=claim['provider_id'])
        emit(con, [generated,
                   event(sid, 'claim_validated', f'#{seq} {claim["claim_id"]} passed validation ({len(warnings)} warning(s)).', stage='validation', payload={'warnings': warnings}, **refs),
                   event(sid, 'claim_persisted', f'#{seq} {claim["claim_id"]} committed to PostgreSQL with {len(bundle["claim_lines"])} line(s), {len(bundle["supply_items"])} supply item(s){", 1 encounter" if bundle["encounters"] else ""}.', stage='persistence', payload={'service_date': claim['service_date'], 'member_id': claim['member_id'], 'scenario_label': label}, **refs)])
    return {'sequence': seq, 'claim_id': claim['claim_id'], 'accepted': True}

def layer_a_backlog(con, sid):
    return con.execute(select(func.count()).select_from(m.stream_claims).where(m.stream_claims.c.session_id == sid, m.stream_claims.c.analysis_status.in_(AWAITING_LAYER_A))).scalar()

def advance(session_id):
    """Lifecycle transitions: STOPPING/RUNNING -> DRAINING when generation ends; DRAINING -> COMPLETED/STOPPED when every
    accepted claim is terminal (fully analyzed or failed after retries)."""
    with engine.begin() as con:
        s = con.execute(select(m.stream_sessions).where(m.stream_sessions.c.session_id == session_id).with_for_update()).mappings().first()
        if not s or s['status'] not in m.STREAM_ACTIVE: return s and s['status']
        sid = session_id; events = []
        if s['stop_requested'] and s['started_at'] is None:
            con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).values(status='STOPPED', stopped_at=now(), updated_at=now()))
            con.execute(update(m.batches).where(m.batches.c.batch_id == s['batch_id']).values(status='COMPLETED', progress=100, completed_at=now(), report={'generated': 0}))
            emit(con, [event(sid, 'session_stopped', f'{sid} stopped before any claim was generated.')]); return 'STOPPED'
        if s['status'] in ('RUNNING', 'STOPPING') and (s['stop_requested'] or s['generated_count'] >= s['intended_count']):
            con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).values(status='DRAINING', updated_at=now()))
            pending = con.execute(select(func.count()).select_from(m.stream_claims).where(m.stream_claims.c.session_id == sid, ~m.stream_claims.c.analysis_status.in_(TERMINAL_CLAIM))).scalar()
            events.append(event(sid, 'generation_stopped' if s['stop_requested'] else 'generation_complete', f'Generation ended at {s["generated_count"]} of {s["intended_count"]} claims; draining {pending} claim(s) still in analysis.', payload={'generated': s['generated_count'], 'pending': pending}))
            emit(con, events); return 'DRAINING'
        if s['status'] == 'DRAINING':
            counts = dict(con.execute(select(m.stream_claims.c.analysis_status, func.count()).where(m.stream_claims.c.session_id == sid).group_by(m.stream_claims.c.analysis_status)).all())
            if sum(v for k, v in counts.items() if k not in TERMINAL_CLAIM): return 'DRAINING'
            if con.execute(select(func.count()).select_from(m.stream_runs).where(m.stream_runs.c.session_id == sid, m.stream_runs.c.status == 'RUNNING')).scalar(): return 'DRAINING'
            final = 'STOPPED' if s['stop_requested'] and s['generated_count'] < s['intended_count'] else 'COMPLETED'
            report = {'generated': s['generated_count'], 'rejected': s['rejected_count'], 'accepted': sum(counts.values()), 'fully_analyzed': counts.get('FULLY_ANALYZED', 0), 'failed': counts.get('FAILED', 0)}
            con.execute(update(m.stream_sessions).where(m.stream_sessions.c.session_id == sid).values(status=final, stopped_at=now(), updated_at=now()))
            con.execute(update(m.batches).where(m.batches.c.batch_id == s['batch_id']).values(status='COMPLETED', progress=100, completed_at=now(), report=report))
            emit(con, [event(sid, 'session_completed', f'{sid} {final.lower()}: {report["accepted"]} accepted, {report["fully_analyzed"]} fully analyzed, {report["failed"]} failed, {report["rejected"]} rejected.', payload=report)])
            return final
        return s['status']

# ---------------------------------------------------------------- Layer A: immediate screening
_thresholds = {'value': None, 'at': 0.0}
_threshold_lock = threading.Lock()
def thresholds(max_age=600):
    """Utilization peer thresholds from the stored claims history, cached and refreshed every ten minutes (a single
    claim does not move procedure/complexity quartiles; the full analysis recomputes them exactly)."""
    with _threshold_lock:
        if _thresholds['value'] is None or time.monotonic() - _thresholds['at'] > max_age:
            c = m.tables['claims']
            with engine.connect() as con:
                rows = [dict(r) for r in con.execute(select(c.c.claim_id, c.c.member_id, c.c.primary_code, c.c.complexity_band, c.c.service_date)).mappings()]
            _, peer_counts = utilization_observations(rows)
            _thresholds.update(value=utilization_thresholds(peer_counts), at=time.monotonic())
        return _thresholds['value']

def rows_where(con, table, *conditions):
    return [dict(r) for r in con.execute(select(m.tables[table]).where(*conditions)).mappings()]

def rule_subset(con, claim):
    """The stored records any Claim Rule needs to evaluate this claim: duplicate candidates (and corrections of them),
    the member's same-procedure claims in the 30-day utilization window, the provider's appointments that day, encounters,
    lines and policies. Bounded, indexed queries; no full-table scan per claim."""
    C = m.tables['claims']; E = m.tables['encounters']
    dup = rows_where(con, 'claims', C.c.member_id == claim['member_id'], C.c.provider_id == claim['provider_id'], C.c.service_date == claim['service_date'])
    util = rows_where(con, 'claims', C.c.member_id == claim['member_id'], C.c.primary_code == claim['primary_code'], C.c.service_date >= claim['service_date'] - timedelta(days=30), C.c.service_date <= claim['service_date'])
    day_encounters = rows_where(con, 'encounters', E.c.provider_id == claim['provider_id'], E.c.service_start >= claim['service_start'] - timedelta(days=1), E.c.service_start <= claim['service_start'] + timedelta(days=1))
    schedule = rows_where(con, 'claims', C.c.encounter_id.in_([e['encounter_id'] for e in day_encounters])) if day_encounters else []
    claims = {c['claim_id']: c for c in dup + util + schedule}; claims[claim['claim_id']] = claim
    corrections = rows_where(con, 'claims', C.c.correction_of_claim_id.in_(list(claims)))
    for c in corrections: claims[c['claim_id']] = c
    encounter_ids = {c['encounter_id'] for c in claims.values() if c['encounter_id']}
    encounters = {e['encounter_id']: e for e in day_encounters}
    for e in rows_where(con, 'encounters', E.c.encounter_id.in_(list(encounter_ids - set(encounters)))): encounters[e['encounter_id']] = e
    return {'claims': list(claims.values()), 'encounters': list(encounters.values()), 'claim_lines': rows_where(con, 'claim_lines', m.tables['claim_lines'].c.claim_id.in_(list(claims))), 'billing_policies': rows_where(con, 'billing_policies')}

def supply_subset(con, claim):
    """The claim's supply items and estimates plus the stored peer items for the same procedure, complexity and supply code."""
    S = m.tables['supply_items']; C = m.tables['claims']
    own = rows_where(con, 'supply_items', S.c.claim_id == claim['claim_id']); estimates = rows_where(con, 'claim_estimates', m.tables['claim_estimates'].c.claim_id == claim['claim_id'])
    if not own and not estimates: return None
    peers = [dict(r) for r in con.execute(select(S).join(C, C.c.claim_id == S.c.claim_id).where(C.c.primary_code == claim['primary_code'], C.c.complexity_band == claim['complexity_band'], S.c.supply_code.in_([s['supply_code'] for s in own]))).mappings()] if own else []
    items = {s['supply_id']: s for s in peers + own}
    claims = {c['claim_id']: c for c in rows_where(con, 'claims', C.c.claim_id.in_({s['claim_id'] for s in items.values()}))} if items else {}
    claims[claim['claim_id']] = claim
    return {'claims': list(claims.values()), 'supply_items': list(items.values()), 'claim_estimates': estimates, 'billing_policies': rows_where(con, 'billing_policies'), 'own_supply_ids': [s['supply_id'] for s in own]}

def screen(claim_id):
    """Layer A computation (read-only): the existing Claim Rules and SupplyTrace engines on the scoped record sets."""
    started = time.monotonic()
    with engine.connect() as con:
        claim = dict(con.execute(select(m.tables['claims']).where(m.tables['claims'].c.claim_id == claim_id)).mappings().one())
        scoped = rule_subset(con, claim); supply = supply_subset(con, claim)
    rule_out = [o for o in rules(scoped, thresholds()) if claim_id in o[0].related_claim_ids]
    rule_result = {'evaluated_rules': ['duplicate_billing', 'missing_encounter_indicator', 'upcoding_indicator', 'unbundling_indicator', 'early_refill', 'impossible_timing', 'excessive_utilization'], 'compared_records': len(scoped['claims']) - 1, 'findings': [{'finding_id': f.finding_id, 'finding_type': f.finding_type, 'related_claim_ids': f.related_claim_ids} for f, _ in rule_out]}
    if supply is None:
        supply_out, supply_status = [], ('NOT_APPLICABLE', 'No itemized supply items or estimates on this claim.', {})
    else:
        out, comparisons = supply_analysis(supply)
        supply_out = [o for o in out if claim_id in o[0].related_claim_ids]
        own = {sid: comparisons[sid] for sid in supply['own_supply_ids']}
        peerless = [sid for sid, c in own.items() if not c['quantity']['available']]
        result = {'comparisons': own, 'findings': [{'finding_id': f.finding_id, 'finding_type': f.finding_type} for f, _ in supply_out], 'peer_items_compared': len(supply['supply_items']) - len(own)}
        if own and len(peerless) == len(own): supply_status = ('INSUFFICIENT_DATA', 'Fewer than 20 matched peer items for every supply code; no peer comparison possible.', result)
        else: supply_status = ('COMPLETED', f'{len(own)} supply item(s) compared with matched peers' + (f'; {len(peerless)} without enough peers' if peerless else '') + '.', result)
    return {'claim': claim, 'outputs': rule_out + supply_out, 'rules': rule_result, 'rule_findings': len(rule_out), 'supply': supply_status, 'supply_findings': len(supply_out), 'seconds': round(time.monotonic() - started, 3)}

def take_claim(runner_id):
    """Leases the next claim awaiting immediate screening (SKIP LOCKED, durable queue in PostgreSQL)."""
    with engine.begin() as con:
        sc = m.stream_claims
        row = con.execute(select(sc).where(or_(and_(sc.c.analysis_status.in_(['ACCEPTED', 'RETRYING']), or_(sc.c.next_attempt_at.is_(None), sc.c.next_attempt_at <= now())), and_(sc.c.analysis_status == 'PROCESSING', sc.c.lease_until < now()))).order_by(sc.c.ingested_at, sc.c.claim_id).with_for_update(skip_locked=True).limit(1)).mappings().first()
        if not row: return None
        if row['attempts'] >= MAX_ATTEMPTS:
            con.execute(update(sc).where(sc.c.claim_id == row['claim_id']).values(analysis_status='FAILED', lease_until=None, error=row['error'] or 'Retry limit reached after interruption.'))
            emit(con, [event(row['session_id'], 'processing_failed', f'{row["claim_id"]} failed immediate screening after {row["attempts"]} attempts; it is not marked fully analyzed.', claim_id=row['claim_id'], stage='claim_rules')]); return None
        con.execute(update(sc).where(sc.c.claim_id == row['claim_id']).values(analysis_status='PROCESSING', attempts=row['attempts'] + 1, lease_until=now() + timedelta(seconds=60), current_stage='claim_rules'))
        st = m.stream_stages
        con.execute(update(st).where(st.c.claim_id == row['claim_id'], st.c.stage.in_(['claim_rules', 'supplytrace'])).values(status='RETRYING' if row['attempts'] else 'RUNNING', attempts=st.c.attempts + 1, started_at=now(), error=None))
        emit(con, [event(row['session_id'], 'analysis_started', f'{row["claim_id"]} immediate screening started (Claim Rules, SupplyTrace)' + (f', attempt {row["attempts"] + 1}.' if row['attempts'] else '.'), claim_id=row['claim_id'], stage='claim_rules')])
        return dict(row, attempts=row['attempts'] + 1)

def set_stage(con, claim_id, stage, status, reason=None, result=None, findings_count=None, error=None, run_id=None, merge=False):
    values = dict(status=status, reason=reason, error=error)
    if status in TERMINAL_OK or status == 'FAILED': values['completed_at'] = now()
    if result is not None: values['result'] = clean(result) if not merge else m.stream_stages.c.result.op('||')(clean(result))
    if findings_count is not None: values['findings_count'] = findings_count
    if run_id: values['run_id'] = run_id
    con.execute(update(m.stream_stages).where(m.stream_stages.c.claim_id == claim_id, m.stream_stages.c.stage == stage).values(**values))

def case_events(con, sid, links, claim_id=None, provider_id=None):
    out = []
    for cid in links['created']: out.append(event(sid, 'case_created', f'New case {cid} created from live-stream findings.', case_id=cid, claim_id=claim_id, provider_id=provider_id))
    for cid in links['updated']: out.append(event(sid, 'case_updated', f'Existing case {cid} updated with live-stream findings.', case_id=cid, claim_id=claim_id, provider_id=provider_id))
    for cid, r in links['rankings'].items():
        out.append(event(sid, 'priority_recalculated', f'{cid} SIU priority {r["priority_score"]:.1f} (queue #{queue_position(con, cid) or "—"}), exposure ${r["potential_financial_exposure"]:,.0f}, computed by the existing scorer.', case_id=cid, claim_id=claim_id, payload={'priority_score': r['priority_score'], 'queue_position': queue_position(con, cid), 'severity': r['severity'], 'exposure': r['potential_financial_exposure']}))
    return out

def finding_events(sid, outputs, created, claim_id=None):
    return [event(sid, 'finding_created', f'{f.engine} finding · {f.finding_type.replace("_", " ")} · {f.severity}: {f.explanation}', finding_id=f.finding_id, claim_id=claim_id or (f.related_claim_ids[0] if f.related_claim_ids else None), provider_id=(f.related_provider_ids or [None])[0], payload={'finding_type': f.finding_type, 'engine': f.engine, 'severity': f.severity, 'related_claim_ids': f.related_claim_ids[:20], 'entity_id': f.entity_id, 'entity_type': f.entity_type}) for f, _ in outputs if f.finding_id in created]

def commit_screening(row, result, batch_id):
    from app.services.pipeline import persist_findings
    claim_id, sid = row['claim_id'], row['session_id']
    with engine.begin() as con:
        con.exec_driver_sql(f'SELECT pg_advisory_xact_lock({LOCK_CASES})')
        current = con.execute(select(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).with_for_update()).mappings().first()
        if current['analysis_status'] != 'PROCESSING' or current['attempts'] != row['attempts']: return False
        outputs = result['outputs']; created = persist_findings(con, outputs, batch_id)
        links = link_incremental(con, outputs) if outputs else {'created': [], 'updated': [], 'finding_cases': {}, 'rankings': {}}
        set_stage(con, claim_id, 'claim_rules', 'COMPLETED', f'{result["rule_findings"]} finding(s) from {len(result["rules"]["evaluated_rules"])} rules against {result["rules"]["compared_records"]} related stored claim(s).', result['rules'], result['rule_findings'])
        status_, reason, supply_result = result['supply']
        set_stage(con, claim_id, 'supplytrace', status_, reason, supply_result, result['supply_findings'])
        fids = sorted({f.finding_id for f, _ in outputs}); cids = sorted(set(links['finding_cases'].values()))
        immediate = {'immediate': {'findings': fids, 'new_findings': created, 'cases': cids, 'case_rankings': links['rankings']}}
        for stage, note in [('evidence_fusion', 'Immediate findings persisted; final fusion runs after background enrichment.'), ('case_consolidation', 'Immediate case links applied; final reconciliation after background enrichment.'), ('siu_ranking', 'Affected cases recalculated; final ranking after background enrichment.')]:
            set_stage(con, claim_id, stage, 'PENDING', note, immediate, merge=True)
        con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).values(analysis_status='IMMEDIATE_DONE', immediate_at=func.clock_timestamp(), lease_until=None, current_stage='awaiting_enrichment', finding_ids=fids, case_ids=cids, error=None))
        c = result['claim']; refs = dict(claim_id=claim_id, provider_id=c['provider_id'])
        events = [event(sid, 'rules_completed', f'{claim_id} Claim Rules complete: {result["rule_findings"]} finding(s).', stage='claim_rules', payload={'findings': result['rules']['findings']}, **refs),
                  event(sid, 'supplytrace_completed' if status_ != 'NOT_APPLICABLE' else 'supplytrace_not_applicable', f'{claim_id} SupplyTrace {status_.replace("_", " ").lower()}: {reason}', stage='supplytrace', payload={'findings': result['supply_findings']}, **refs)]
        events += finding_events(sid, outputs, created, claim_id) + case_events(con, sid, links, claim_id, c['provider_id'])
        events.append(event(sid, 'immediate_completed', f'{claim_id} immediate screening done in {result["seconds"]:.2f}s; awaiting background enrichment (graph, radar, Phoenix, ML, forecast).', stage='claim_rules', payload={'seconds': result['seconds'], 'findings': fids, 'cases': cids}, **refs))
        emit(con, events)
    return True

def fail_screening(row, exc):
    claim_id, sid = row['claim_id'], row['session_id']; terminal = row['attempts'] >= MAX_ATTEMPTS
    with engine.begin() as con:
        con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).values(analysis_status='FAILED' if terminal else 'RETRYING', lease_until=None, next_attempt_at=now() + timedelta(seconds=3 * row['attempts']), error=f'{type(exc).__name__}: {str(exc)[:300]}'))
        for stage in ['claim_rules', 'supplytrace']: set_stage(con, claim_id, stage, 'FAILED' if terminal else 'RETRYING', error=f'{type(exc).__name__}: {str(exc)[:300]}')
        emit(con, [event(sid, 'processing_failed', f'{claim_id} immediate screening failed ({type(exc).__name__}); ' + ('retry limit reached, claim marked FAILED.' if terminal else f'retry {row["attempts"] + 1} of {MAX_ATTEMPTS} scheduled.'), claim_id=claim_id, stage='claim_rules', payload={'error': str(exc)[:300], 'attempt': row['attempts']})])

def process_next(runner_id, screen_fn=None):
    row = take_claim(runner_id)
    if not row: return None
    batch_id = session_batch(row['session_id'])
    try:
        result = (screen_fn or screen)(row['claim_id'])
        commit_screening(row, result, batch_id)
    except Exception as exc:
        log.exception('Immediate screening failed for %s', row['claim_id']); fail_screening(row, exc)
    return row['claim_id']

def session_batch(session_id):
    with engine.connect() as con: return con.execute(select(m.stream_sessions.c.batch_id).where(m.stream_sessions.c.session_id == session_id)).scalar()

# ---------------------------------------------------------------- Layer B: background enrichment (worker job)
def enqueue_enrichment(session_id, reason):
    """Schedules one stream_enrichment job on the existing worker queue unless one is already queued or running."""
    from app.worker import enqueue_kind
    with engine.begin() as con:
        awaiting = con.execute(select(func.count()).select_from(m.stream_claims).where(m.stream_claims.c.session_id == session_id, m.stream_claims.c.analysis_status.in_(AWAITING_ENRICHMENT))).scalar()
        if not awaiting: return None
        job, created = enqueue_kind(con, 'stream_enrichment', {'session_id': session_id})
        if created: emit(con, [event(session_id, 'enrichment_scheduled', f'Background enrichment scheduled on the analytics worker for {awaiting} claim(s) ({reason}).', payload={'job_id': job['job_id'], 'claims': awaiting})])
        return job

def flagged_claims_by_provider(con):
    """Independent (non-graph) active findings per provider, for the graph referral rule's corroboration requirement."""
    q = select(m.tables['claims'].c.provider_id, m.finding_claims.c.claim_id).select_from(m.finding_claims.join(m.findings).join(m.tables['claims'], m.tables['claims'].c.claim_id == m.finding_claims.c.claim_id)).where(m.findings.c.status.in_(ACTIVE_FINDINGS), m.findings.c.engine != 'graph')
    out = {}
    for pid, cid in con.execute(q).all(): out.setdefault(pid, []).append(cid)
    return [(SimpleNamespace(related_provider_ids=[pid], related_claim_ids=cids), None) for pid, cids in out.items()]

def run_enrichment(session_id, job_id=None):
    """Layer B micro-batch. Covers exactly the claims selected at the start (recorded with the run); engines run scoped to
    their providers/members with full-population context where an engine normalizes against peers."""
    from app.services.repository import load_data
    from app.services import radar, phoenix
    from app.services.ml import infer_anomaly, infer_forecast
    from app.services.graph import referral_findings, neighborhood
    from app.services.pipeline import persist_findings, persist_links
    started = time.monotonic(); run_id = 'RUN-' + uuid4().hex[:12]
    with engine.begin() as con:
        sc = m.stream_claims
        con.execute(update(m.stream_runs).where(m.stream_runs.c.session_id == session_id, m.stream_runs.c.status == 'RUNNING').values(status='INTERRUPTED', error='Superseded by a new run after worker interruption.', completed_at=now()))
        rows = [dict(r) for r in con.execute(select(sc).where(sc.c.session_id == session_id, sc.c.analysis_status.in_(AWAITING_ENRICHMENT)).order_by(sc.c.sequence).with_for_update()).mappings()]
        if not rows: return {'run_id': None, 'claims': 0}
        exhausted = [r for r in rows if con.execute(select(func.max(m.stream_stages.c.attempts)).where(m.stream_stages.c.claim_id == r['claim_id'], m.stream_stages.c.stage.in_(ENRICHMENT_STAGES[:5]))).scalar() >= MAX_ATTEMPTS]
        for r in exhausted:
            con.execute(update(sc).where(sc.c.claim_id == r['claim_id']).values(analysis_status='FAILED', error=r['error'] or 'Background enrichment retry limit reached.'))
        rows = [r for r in rows if r not in exhausted]
        claim_ids = [r['claim_id'] for r in rows]
        claims = {c['claim_id']: dict(c) for c in con.execute(select(m.tables['claims']).where(m.tables['claims'].c.claim_id.in_(claim_ids))).mappings()}
        providers = sorted({c['provider_id'] for c in claims.values()}); members = sorted({c['member_id'] for c in claims.values()})
        if rows:
            con.execute(m.stream_runs.insert().values(run_id=run_id, session_id=session_id, job_id=job_id, status='RUNNING', claim_ids=claim_ids, watermark_sequence=max(r['sequence'] for r in rows), providers=providers, stage_results={}))
            con.execute(update(sc).where(sc.c.claim_id.in_(claim_ids)).values(analysis_status='ENRICHING', enrichment_run_id=run_id, current_stage='enrichment'))
            st = m.stream_stages
            con.execute(update(st).where(st.c.claim_id.in_(claim_ids), st.c.stage.in_(ENRICHMENT_STAGES[:5]), ~st.c.status.in_(TERMINAL_OK)).values(status=case((st.c.attempts > 0, 'RETRYING'), else_='RUNNING'), attempts=st.c.attempts + 1, started_at=now(), run_id=run_id, error=None))
        emit(con, [event(session_id, 'processing_failed', f'{r["claim_id"]} background enrichment failed {MAX_ATTEMPTS} times; marked FAILED (not fully analyzed).', claim_id=r['claim_id'], stage='enrichment') for r in exhausted]
             + ([event(session_id, 'enrichment_started', f'Enrichment run {run_id} started for {len(rows)} claim(s) (sequence ≤ {max(r["sequence"] for r in rows)}) across providers {", ".join(providers)}.', payload={'run_id': run_id, 'claims': claim_ids, 'providers': providers, 'members': len(members)})] if rows else []))
    if not rows: return {'run_id': None, 'claims': 0, 'failed': len(exhausted)}
    try:
        with engine.connect() as con:
            data = load_data(con)
            data['successor_links'] = [clean(dict(r)) for r in con.execute(select(m.successors).where(m.successors.c.flagged.is_(True))).mappings()]
            stored_peers = {r['provider_id']: r['metrics'] for r in con.execute(select(m.radar_batches.c.provider_id, m.radar_batches.c.metrics)).mappings()}
            independent = flagged_claims_by_provider(con)
            existing = {(r['finding_type'], r['entity_id'], r['rule_or_model_version']): r['finding_id'] for r in con.execute(select(m.findings.c.finding_type, m.findings.c.entity_id, m.findings.c.rule_or_model_version, m.findings.c.finding_id).where(m.findings.c.entity_id.in_(providers + members), m.findings.c.engine.in_(['ml', 'radar', 'phoenix', 'graph']))).mappings()}
        loaded = time.monotonic() - started
        engines, outputs = {}, []
        def run(name, fn):
            t = time.monotonic()
            try: engines[name] = {'status': 'OK', **fn(), 'seconds': round(time.monotonic() - t, 3)}
            except Exception as exc:
                log.exception('Enrichment engine %s failed', name); engines[name] = {'status': 'FAILED', 'error': f'{type(exc).__name__}: {str(exc)[:300]}', 'seconds': round(time.monotonic() - t, 3)}
        def isolation():
            found, rows_, report = infer_anomaly(data, providers); outputs.extend(found); return {'rows': rows_, 'report': report, 'findings': [f.finding_id for f, _ in found]}
        def graph():
            found = referral_findings(data, independent, set(providers)); outputs.extend(found)
            return {'neighborhoods': {pid: neighborhood(data, pid) for pid in providers}, 'findings': [f.finding_id for f, _ in found]}
        def member_radar():
            found, member_rows, batch_rows, batch_member_rows = radar.analyze(data, set(providers), set(members), stored_peers); outputs.extend(found)
            return {'member_rows': member_rows, 'batch_rows': batch_rows, 'batch_member_rows': batch_member_rows, 'findings': [f.finding_id for f, _ in found]}
        def phoenix_():
            eligibility = {pid: phoenix.eligibility(data, pid) for pid in providers}; eligible = {pid for pid, e in eligibility.items() if e['eligible']}
            found, links = phoenix.analyze(data, eligible) if eligible else ([], []); outputs.extend(found)
            return {'eligibility': eligibility, 'links': links, 'findings': [f.finding_id for f, _ in found]}
        def forecast():
            rows_, report = infer_forecast(data, providers); return {'rows': rows_, 'report': report}
        for name, fn in [('isolation_forest', isolation), ('nexus_graph', graph), ('member_radar', member_radar), ('phoenix', phoenix_), ('forecast', forecast)]: run(name, fn)
        # Provider/member-level findings whose content-derived ID drifts as new claims arrive (e.g. the top-10 claims of an
        # anomaly) are not duplicated: an existing finding of the same type, entity and model version is retained.
        kept, retained = [], {}
        for f, ev in {f.finding_id: (f, ev) for f, ev in outputs}.values():
            prior = existing.get((f.finding_type, f.entity_id, f.rule_or_model_version))
            if prior and prior != f.finding_id: retained[f.finding_id] = prior
            else: kept.append((f, ev))
        compute_seconds = time.monotonic() - started
        return finalize(session_id, run_id, rows, claims, providers, engines, kept, retained, {'load_seconds': round(loaded, 3), 'compute_seconds': round(compute_seconds, 3)}, started, persist_findings, persist_links)
    except Exception as exc:
        log.exception('Enrichment run %s failed', run_id)
        with engine.begin() as con:
            con.execute(update(m.stream_runs).where(m.stream_runs.c.run_id == run_id).values(status='FAILED', error=f'{type(exc).__name__}: {str(exc)[:500]}', completed_at=now(), duration_seconds=round(time.monotonic() - started, 3)))
            con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id.in_(claim_ids)).values(analysis_status='ENRICH_RETRY'))
            st = m.stream_stages
            con.execute(update(st).where(st.c.claim_id.in_(claim_ids), st.c.stage.in_(ENRICHMENT_STAGES[:5]), ~st.c.status.in_(TERMINAL_OK)).values(status='FAILED', error=f'{type(exc).__name__}: {str(exc)[:300]}'))
            emit(con, [event(session_id, 'processing_failed', f'Enrichment run {run_id} failed ({type(exc).__name__}); {len(claim_ids)} claim(s) will be retried.', stage='enrichment', payload={'run_id': run_id, 'error': str(exc)[:300]})])
        raise

def upsert_predictions(con, rows):
    for r in rows:
        stmt = insert(m.predictions).values(**clean(r))
        con.execute(stmt.on_conflict_do_update(index_elements=['prediction_id'], set_={'value': stmt.excluded.value, 'details': stmt.excluded.details}))

def finalize(session_id, run_id, rows, claims, providers, engines, kept, retained, timing, started, persist_findings, persist_links):
    st = m.stream_stages; sc = m.stream_claims
    with engine.begin() as con:
        con.exec_driver_sql(f'SELECT pg_advisory_xact_lock({LOCK_CASES})')
        run = con.execute(select(m.stream_runs).where(m.stream_runs.c.run_id == run_id).with_for_update()).mappings().first()
        if run['status'] != 'RUNNING': return {'run_id': run_id, 'status': run['status']}
        batch_id = con.execute(select(m.stream_sessions.c.batch_id).where(m.stream_sessions.c.session_id == session_id)).scalar()
        created = persist_findings(con, kept, batch_id)
        e = engines
        if e['isolation_forest']['status'] == 'OK': upsert_predictions(con, e['isolation_forest']['rows'])
        if e['forecast']['status'] == 'OK': upsert_predictions(con, e['forecast']['rows'])
        if e['member_radar']['status'] == 'OK':
            r = e['member_radar']; scope = {b['provider_id'] for b in r['batch_rows']} | set(providers)
            for row in r['member_rows']:
                stmt = insert(m.member_risk).values(**clean(dict(row, batch_id=batch_id)))
                con.execute(stmt.on_conflict_do_update(index_elements=['member_id'], set_={k: stmt.excluded[k] for k in ['score', 'as_of', 'signals', 'detector_version', 'batch_id']}))
            con.execute(delete(m.radar_batch_members).where(m.radar_batch_members.c.provider_id.in_(scope))); con.execute(delete(m.radar_batches).where(m.radar_batches.c.provider_id.in_(scope)))
            if r['batch_rows']: con.execute(m.radar_batches.insert(), [clean(dict(b, finding_id=retained.get(b['finding_id'], b['finding_id']), batch_id=batch_id)) for b in r['batch_rows']])
            if r['batch_member_rows']: con.execute(m.radar_batch_members.insert(), [clean(b) for b in r['batch_member_rows']])
        if e['phoenix']['status'] == 'OK' and e['phoenix']['links']: persist_links(con, e['phoenix']['links'], batch_id)
        links = link_incremental(con, kept) if kept else {'created': [], 'updated': [], 'finding_cases': {}, 'rankings': {}}
        # Forecast values feed the scorer's recurrence factor: recalculate open cases of the affected providers.
        for cid in con.execute(select(m.cases.c.case_id).where(m.cases.c.primary_entity.in_(providers), ~m.cases.c.case_status.in_(['CLOSED', 'RESOLVED']))).scalars():
            if cid not in links['rankings']: links['rankings'][cid] = recalculate(con, cid)
        events = [event(session_id, 'finding_retained', f'{fid} not duplicated: existing {prior} (same finding type, entity and model version) retained.', finding_id=prior) for fid, prior in retained.items()]
        events += finding_events(session_id, kept, created)
        events += case_events(con, session_id, links, provider_id=None)
        events += engine_events(session_id, engines, providers)
        complete, failed = 0, 0
        for r in rows:
            cid = r['claim_id']; c = claims[cid]; pid = c['provider_id']
            statuses = per_claim_stages(con, c, engines, providers)
            by_engine = dict(con.execute(select(m.findings.c.engine, func.count()).join(m.finding_claims).where(m.finding_claims.c.claim_id == cid).group_by(m.findings.c.engine)).all())
            for stage, engine_name in [('isolation_forest', 'ml'), ('nexus_graph', 'graph'), ('member_radar', 'radar'), ('phoenix', 'phoenix')]:
                status_, reason, result, _ = statuses[stage]; statuses[stage] = (status_, reason, result, by_engine.get(engine_name, 0))
            for stage, (status_, reason, result, count) in statuses.items(): set_stage(con, cid, stage, status_, reason, result, count, error=reason if status_ == 'FAILED' else None, run_id=run_id)
            fids = sorted(con.execute(select(m.finding_claims.c.finding_id).where(m.finding_claims.c.claim_id == cid)).scalars())
            case_ids = sorted(set(con.execute(select(m.case_findings.c.case_id).where(m.case_findings.c.finding_id.in_(fids))).scalars())) if fids else []
            set_stage(con, cid, 'evidence_fusion', 'COMPLETED', f'{len(fids)} finding(s) reference this claim after all engines.', {'final': {'findings': fids}}, len(fids), run_id=run_id)
            if case_ids:
                set_stage(con, cid, 'case_consolidation', 'COMPLETED', f'Linked to {", ".join(case_ids)}.', {'final': {'cases': case_ids}}, run_id=run_id)
                ranks = {k: {'priority_score': con.execute(select(m.cases.c.priority_score).where(m.cases.c.case_id == k)).scalar(), 'queue_position': queue_position(con, k)} for k in case_ids}
                set_stage(con, cid, 'siu_ranking', 'COMPLETED', '; '.join(f'{k}: {v["priority_score"]:.1f} (#{v["queue_position"] or "—"})' for k, v in ranks.items()), {'final': ranks}, run_id=run_id)
            else:
                set_stage(con, cid, 'case_consolidation', 'NOT_APPLICABLE', 'No finding references this claim; ordinary claims are not placed in cases.', {'final': {'cases': []}}, run_id=run_id)
                set_stage(con, cid, 'siu_ranking', 'NOT_APPLICABLE', 'Claim is in no case, so there is no SIU priority to recalculate.', {'final': {}}, run_id=run_id)
            stage_status = dict(con.execute(select(st.c.stage, st.c.status).where(st.c.claim_id == cid)).all())
            if all(stage_status.get(s) in TERMINAL_OK for s in STAGE_NAMES):
                con.execute(update(sc).where(sc.c.claim_id == cid).values(analysis_status='FULLY_ANALYZED', fully_analyzed_at=func.clock_timestamp(), current_stage=None, finding_ids=fids, case_ids=case_ids, error=None)); complete += 1
                events.append(event(session_id, 'claim_fully_analyzed', f'{cid} fully analyzed: all 12 stages terminal · {len(fids)} finding(s){", case " + ", ".join(case_ids) if case_ids else ""}.', claim_id=cid, provider_id=pid, payload={'findings': fids, 'cases': case_ids}))
            else:
                bad = [s for s in STAGE_NAMES if stage_status.get(s) not in TERMINAL_OK]
                con.execute(update(sc).where(sc.c.claim_id == cid).values(analysis_status='ENRICH_RETRY', current_stage=bad[0], finding_ids=fids, case_ids=case_ids, error='Stages not complete: ' + ', '.join(bad))); failed += 1
        duration = round(time.monotonic() - started, 3)
        summary = {k: {kk: vv for kk, vv in v.items() if kk not in ('rows', 'member_rows', 'batch_rows', 'batch_member_rows', 'links')} for k, v in engines.items()}
        con.execute(update(m.stream_runs).where(m.stream_runs.c.run_id == run_id).values(status='COMPLETED', completed_at=now(), duration_seconds=duration, stage_results=clean({'engines': summary, 'timing': timing, 'new_findings': created, 'retained': retained, 'fully_analyzed': complete, 'retry': failed})))
        events.append(event(session_id, 'enrichment_completed', f'Enrichment run {run_id} complete in {duration:.2f}s: {len(rows)} claim(s), {len(created)} new finding(s), {complete} fully analyzed' + (f', {failed} to retry' if failed else '') + '.', payload={'run_id': run_id, 'duration_seconds': duration, 'timing': timing, 'new_findings': created, 'fully_analyzed': complete, 'retry': failed}))
        emit(con, events)
    return {'run_id': run_id, 'claims': len(rows), 'new_findings': len(created), 'fully_analyzed': complete, 'retry': failed, 'duration_seconds': duration}

def engine_events(sid, engines, providers):
    out = []
    for name, label in [('isolation_forest', 'Isolation Forest inference'), ('nexus_graph', 'Nexus Graph update'), ('member_radar', 'Member Radar'), ('phoenix', 'Phoenix'), ('forecast', 'Forecast inference')]:
        e = engines[name]
        if e['status'] != 'OK':
            out.append(event(sid, 'processing_failed', f'{label} failed: {e["error"]}. Affected claims are not fully analyzed and will be retried.', stage=name, payload={'error': e['error']})); continue
        for pid in providers:
            extra = {}
            if name == 'member_radar':
                b = next((b for b in e['batch_rows'] if b['provider_id'] == pid), None); extra = {'radar_candidate': bool(b), 'qualified': bool(b and b['qualified'])}
            if name == 'phoenix': extra = {'flagged_links': [l['link_id'] for l in e['links'] if l['flagged'] and pid in (l['predecessor_id'], l['successor_id'])]}
            out.append(event(sid, f'{name}_completed', f'{label} · {pid}: {engine_summary(name, e, pid)}', stage=name, provider_id=pid, payload={'seconds': e['seconds'], **extra}))
    return out

def engine_summary(name, e, pid):
    if name == 'isolation_forest':
        p = e['report']['providers'].get(pid, {})
        if not (p.get('eligible') and 'percentile' in p): return 'insufficient history (fewer than 3 claims in the 90-day feature window); no anomaly score.'
        return f'anomaly percentile {p["percentile"]:.1%} with stored model {e["report"]["model_version"]} (finding threshold 95%).'
    if name == 'nexus_graph':
        n = e['neighborhoods'][pid]; return f'{n["claims_billed"]} claims, {n["members"]} members, {n["documented_relationships"]} documented relationship(s), {n["referrals_in"] + n["referrals_out"]} referral(s).'
    if name == 'member_radar':
        b = next((b for b in e['batch_rows'] if b['provider_id'] == pid), None)
        if not b: return 'not a new-member batch candidate (fewer than 10 new members in the 90-day window).'
        x = b['metrics']; return f'{x["new_members"]} new members, growth {x["growth_ratio"]:.1f}×, {x["share_without_prior_relationship"]:.0%} without prior relationship, {x["share_with_prior_care_context"]:.0%} with care context → ' + ('QUALIFIED batch.' if b['qualified'] else 'not qualified.')
    if name == 'phoenix':
        el = e['eligibility'][pid]
        if not el['eligible']: return el['reason']
        mine = [l for l in e['links'] if pid in (l['predecessor_id'], l['successor_id'])]
        if not mine: return f'eligible as {"/".join(el["roles"])}, but no pair is comparable yet: a successor needs at least {phoenix_min_claims()} claims within 90 days of enrollment.'
        return f'{len(mine)} predecessor/successor pair(s) evaluated, {sum(l["flagged"] for l in mine)} flagged (best similarity {max(l["score"] for l in mine):.2f}; threshold {phoenix_threshold():.2f}).'
    r = e['report'].get(pid)
    if not r or not r.get('eligible'): return 'insufficient history (fewer than 3 claims in the 90-day feature window); no forecast.'
    return f'30/60/90-day event-recurrence probability {r["30"]:.2f} / {r["60"]:.2f} / {r["90"]:.2f} (stored models).'

def per_claim_stages(con, c, engines, providers):
    """Stage outcome for one claim from the scoped engine results. Engine failure -> FAILED (never COMPLETED)."""
    pid, mid = c['provider_id'], c['member_id']; out = {}
    def failed(name): return ('FAILED', engines[name]['error'], {}, 0)
    e = engines['isolation_forest']
    if e['status'] != 'OK': out['isolation_forest'] = failed('isolation_forest')
    else:
        p = e['report']['providers'].get(pid, {})
        found = [f for f in e['findings']]
        out['isolation_forest'] = ('COMPLETED', f'Provider {pid} anomaly percentile {p["percentile"]:.1%} (stored model {e["report"]["model_version"]}, no retraining).', {'provider': pid, **p, 'model_version': e['report']['model_version'], 'scoring_cutoff': e['report']['scoring_cutoff']}, len(found)) if p.get('eligible') and 'percentile' in p else ('INSUFFICIENT_DATA', f'Provider {pid} has fewer than 3 claims in the 90-day feature window ending {e["report"]["scoring_cutoff"]}; the model does not score it (no anomaly score is invented).', {'provider': pid, **p}, 0)
    e = engines['nexus_graph']
    out['nexus_graph'] = failed('nexus_graph') if e['status'] != 'OK' else ('COMPLETED', f'Neighborhood of {pid} refreshed; referral-concentration rule evaluated.', {'provider': pid, 'neighborhood': e['neighborhoods'][pid], 'findings': e['findings']}, 0)
    e = engines['member_radar']
    if e['status'] != 'OK': out['member_radar'] = failed('member_radar')
    else:
        b = next((b for b in e['batch_rows'] if b['provider_id'] == pid), None); member = next((r for r in e['member_rows'] if r['member_id'] == mid), None)
        out['member_radar'] = ('COMPLETED', engine_summary('member_radar', e, pid), {'provider': pid, 'batch': clean(b) if b else None, 'member_id': mid, 'member_score': member['score'] if member else None}, sum(1 for f in e['findings']))
    e = engines['phoenix']
    if e['status'] != 'OK': out['phoenix'] = failed('phoenix')
    else:
        el = e['eligibility'][pid]
        if not el['eligible'] and el['reason'].startswith('No provider profile'): out['phoenix'] = ('NOT_APPLICABLE', el['reason'], el, 0)
        else: out['phoenix'] = ('COMPLETED', engine_summary('phoenix', e, pid), {'eligibility': el, 'pairs': [{k: l[k] for k in ['link_id', 'predecessor_id', 'successor_id', 'score', 'flagged']} for l in e['links'] if pid in (l['predecessor_id'], l['successor_id'])]}, len(e['findings']))
    e = engines['forecast']
    if e['status'] != 'OK': out['forecast'] = failed('forecast')
    else:
        r = e['report'].get(pid)
        out['forecast'] = ('COMPLETED', engine_summary('forecast', e, pid), {'provider': pid, **r, 'models': e['report'].get('_models')}, 0) if r and r.get('eligible') else ('INSUFFICIENT_DATA', engine_summary('forecast', e, pid), {'provider': pid, 'models': e['report'].get('_models')}, 0)
    return out

# ---------------------------------------------------------------- read models
def percentile(values, q):
    if not values: return None
    values = sorted(values); k = (len(values) - 1) * q; lo = int(k); hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)

def status(session_id):
    with engine.connect() as con:
        s = con.execute(select(m.stream_sessions).where(m.stream_sessions.c.session_id == session_id)).mappings().first()
        if not s: raise LookupError('Stream session not found')
        s = dict(s); sc = m.stream_claims
        counts = dict(con.execute(select(sc.c.analysis_status, func.count()).where(sc.c.session_id == session_id).group_by(sc.c.analysis_status)).all())
        accepted = sum(counts.values()); full = counts.get('FULLY_ANALYZED', 0); failed = counts.get('FAILED', 0)
        analyzed = con.execute(select(func.count()).select_from(sc).where(sc.c.session_id == session_id, sc.c.immediate_at.is_not(None))).scalar()
        times = con.execute(select(sc.c.generated_at, sc.c.ingested_at, sc.c.immediate_at, sc.c.fully_analyzed_at).where(sc.c.session_id == session_id)).all()
        ingest = [(i - g).total_seconds() * 1000 for g, i, _, _ in times]
        immediate = [(a - i).total_seconds() for _, i, a, _ in times if a]
        e2e = [(f - i).total_seconds() for _, i, _, f in times if f]
        ev = m.stream_events
        kinds = dict(con.execute(select(ev.c.event_type, func.count()).where(ev.c.session_id == session_id).group_by(ev.c.event_type)).all())
        updated_cases = con.execute(select(func.count(func.distinct(ev.c.case_id))).where(ev.c.session_id == session_id, ev.c.event_type == 'case_updated')).scalar()
        last = con.execute(select(ev.c.event_id, ev.c.message, ev.c.created_at).where(ev.c.session_id == session_id).order_by(ev.c.event_id.desc()).limit(1)).first()
        runs = [dict(r) for r in con.execute(select(m.stream_runs.c.run_id, m.stream_runs.c.status, m.stream_runs.c.duration_seconds, m.stream_runs.c.created_at, m.stream_runs.c.completed_at, func.jsonb_array_length(m.stream_runs.c.claim_ids).label('claims')).where(m.stream_runs.c.session_id == session_id).order_by(m.stream_runs.c.created_at)).mappings()]
        durations = [r['duration_seconds'] for r in runs if r['status'] == 'COMPLETED' and r['duration_seconds'] is not None]
        oldest = con.execute(select(func.min(sc.c.ingested_at)).where(sc.c.session_id == session_id, ~sc.c.analysis_status.in_(TERMINAL_CLAIM))).scalar()
        first_in, last_in = (min(i for _, i, _, _ in times), max(i for _, i, _, _ in times)) if times else (None, None)
        latest_full = max((f for *_, f in times if f), default=None)
        window = con.execute(select(func.count()).select_from(sc).where(sc.c.session_id == session_id, sc.c.ingested_at > now() - timedelta(seconds=30))).scalar()
        runner = con.execute(select(func.max(m.stream_runners.c.heartbeat_at))).scalar()
        job = con.execute(select(m.jobs.c.job_id, m.jobs.c.status).where(m.jobs.c.kind == 'stream_enrichment', m.jobs.c.status.in_(['QUEUED', 'RUNNING'])).limit(1)).mappings().first()
    elapsed = ((s['stopped_at'] or now()) - s['started_at']).total_seconds() if s['started_at'] else 0
    metrics = {
        'generated': s['generated_count'], 'rejected': s['rejected_count'], 'accepted': accepted, 'analyzed': analyzed, 'fully_analyzed': full, 'failed': failed, 'pending': accepted - full - failed,
        'awaiting_immediate': sum(counts.get(k, 0) for k in AWAITING_LAYER_A), 'awaiting_enrichment': sum(counts.get(k, 0) for k in AWAITING_ENRICHMENT), 'status_counts': counts,
        'new_findings': kinds.get('finding_created', 0), 'cases_created': kinds.get('case_created', 0), 'cases_updated': updated_cases, 'events': sum(kinds.values()),
        'arrival_rate_per_second': round(accepted / max((last_in - first_in).total_seconds(), 1), 3) if accepted > 1 else None, 'arrivals_last_30s': window,
        'throughput_per_minute': round(full / max(elapsed, 1) * 60, 2) if elapsed else None, 'latest_fully_analyzed_at': latest_full,
        'oldest_pending_age_seconds': round((now() - oldest).total_seconds(), 1) if oldest else 0, 'max_backlog': s['max_backlog'],
        'ingestion_latency_ms': {'mean': round(sum(ingest) / len(ingest), 1) if ingest else None, 'p95': round(percentile(ingest, .95), 1) if ingest else None},
        'immediate_latency_s': {'mean': round(sum(immediate) / len(immediate), 3) if immediate else None, 'p95': round(percentile(immediate, .95), 3) if immediate else None},
        'end_to_end_latency_s': {'mean': round(sum(e2e) / len(e2e), 2) if e2e else None, 'p95': round(percentile(e2e, .95), 2) if e2e else None, 'max': round(max(e2e), 2) if e2e else None},
        'enrichment_runs': len(runs), 'micro_batch_seconds': {'mean': round(sum(durations) / len(durations), 3) if durations else None, 'max': max(durations) if durations else None, 'last': durations[-1] if durations else None},
        'worker_errors': kinds.get('processing_failed', 0), 'elapsed_seconds': round(elapsed, 1),
    }
    s.update(metrics=metrics, last_event_id=last[0] if last else 0, last_event=last[1] if last else None, last_event_at=last[2] if last else None, runs=runs[-10:], runner={'alive': bool(runner and (now() - runner).total_seconds() < 20), 'last_heartbeat': runner}, enrichment_job=dict(job) if job else None, fully_analyzed_definition='All 12 stages COMPLETED, NOT_APPLICABLE or INSUFFICIENT_DATA (documented no-prediction outcome); FAILED or pending stages never count.')
    return clean(s)

def claim_coverage(claim_id):
    with engine.connect() as con:
        row = con.execute(select(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id)).mappings().first()
        if not row: raise LookupError('Claim is not a live-stream claim')
        stages = {r['stage']: dict(r) for r in con.execute(select(m.stream_stages).where(m.stream_stages.c.claim_id == claim_id)).mappings()}
        run = con.execute(select(m.stream_runs.c.run_id, m.stream_runs.c.status, m.stream_runs.c.watermark_sequence, m.stream_runs.c.completed_at, m.stream_runs.c.duration_seconds).where(m.stream_runs.c.run_id == row['enrichment_run_id'])).mappings().first() if row['enrichment_run_id'] else None
        claim = con.execute(select(m.tables['claims']).where(m.tables['claims'].c.claim_id == claim_id)).mappings().first()
    ordered = [dict(stages.get(name, {'stage': name, 'status': 'PENDING'}), label=label, layer=layer) for name, label, layer in STAGES]
    full = all(s['status'] in TERMINAL_OK for s in ordered)
    return clean({**dict(row), 'claim': dict(claim), 'scenario_label': gen.SCENARIO_LABELS.get(row['scenario'], row['scenario']), 'scenario_note': 'Demo scenario label from the stream scenario pack; no detection engine reads it.', 'stages': ordered, 'fully_analyzed': full and row['analysis_status'] == 'FULLY_ANALYZED', 'enrichment_run': dict(run) if run else None})

def session_claims(session_id, page=1, size=50):
    with engine.connect() as con:
        sc = m.stream_claims; c = m.tables['claims']
        total = con.execute(select(func.count()).select_from(sc).where(sc.c.session_id == session_id)).scalar()
        rows = [dict(r) for r in con.execute(select(sc, c.c.provider_id, c.c.member_id, c.c.claim_type, c.c.primary_code, c.c.billed_amount_usd).join(c, c.c.claim_id == sc.c.claim_id).where(sc.c.session_id == session_id).order_by(sc.c.sequence.desc()).offset((page - 1) * size).limit(size)).mappings()]
        for r in rows: r['scenario_label'] = gen.SCENARIO_LABELS.get(r['scenario'], r['scenario'])
    return clean({'items': rows, 'total': total, 'page': page, 'page_size': size})

def summary(session_id):
    base = status(session_id)
    with engine.connect() as con:
        ev = m.stream_events
        fids = list(con.execute(select(ev.c.finding_id).where(ev.c.session_id == session_id, ev.c.event_type == 'finding_created')).scalars())
        fs = [dict(r) for r in con.execute(select(m.findings.c.finding_id, m.findings.c.finding_type, m.findings.c.engine, m.findings.c.severity, m.findings.c.status, m.findings.c.entity_id).where(m.findings.c.finding_id.in_(fids))).mappings()]
        cids = set(con.execute(select(ev.c.case_id).where(ev.c.session_id == session_id, ev.c.event_type.in_(['case_created', 'case_updated', 'priority_recalculated']), ev.c.case_id.is_not(None))).scalars())
        cs = [dict(r, queue_position=queue_position(con, r['case_id'])) for r in con.execute(select(m.cases.c.case_id, m.cases.c.title, m.cases.c.priority_score, m.cases.c.severity, m.cases.c.case_status, m.cases.c.potential_financial_exposure).where(m.cases.c.case_id.in_(cids)).order_by(m.cases.c.priority_score.desc())).mappings()]
        stage_counts = [dict(r) for r in con.execute(select(m.stream_stages.c.stage, m.stream_stages.c.status, func.count().label('count')).join(m.stream_claims, m.stream_claims.c.claim_id == m.stream_stages.c.claim_id).where(m.stream_claims.c.session_id == session_id).group_by(m.stream_stages.c.stage, m.stream_stages.c.status)).mappings()]
    return clean({**base, 'findings': fs, 'cases': cs, 'stage_counts': stage_counts})

def list_sessions(limit=20):
    with engine.connect() as con:
        return clean([dict(r) for r in con.execute(select(m.stream_sessions.c.session_id, m.stream_sessions.c.status, m.stream_sessions.c.intended_count, m.stream_sessions.c.generated_count, m.stream_sessions.c.created_at, m.stream_sessions.c.stopped_at, m.stream_sessions.c.seed).order_by(m.stream_sessions.c.session_number.desc()).limit(limit)).mappings()])

def current_session():
    with engine.connect() as con:
        sid = con.execute(select(m.stream_sessions.c.session_id).order_by((m.stream_sessions.c.status.in_(m.STREAM_ACTIVE)).desc(), m.stream_sessions.c.session_number.desc()).limit(1)).scalar()
        alive = runner_alive(con)
    return {'session': status(sid) if sid else None, 'runner_alive': alive, 'defaults': {'rate_per_second': settings.stream_default_rate, 'max_claims': settings.stream_default_count, 'seed': gen.DEFAULT_SEED, 'scenario_mode': 'mixed_demo', 'modes': gen.MODES, 'max_count': settings.stream_max_count, 'enrichment_interval_seconds': settings.stream_enrichment_interval_seconds, 'pack_version': gen.PACK_VERSION}}

def retry_claim(claim_id):
    """Controlled retry of a FAILED claim: failed stages become RETRYING with a fresh attempt budget."""
    with engine.begin() as con:
        row = con.execute(select(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).with_for_update()).mappings().first()
        if not row: raise LookupError('Claim is not a live-stream claim')
        if row['analysis_status'] != 'FAILED': raise Conflict(f'Only FAILED claims can be retried (current: {row["analysis_status"]}).')
        st = m.stream_stages
        failed = list(con.execute(select(st.c.stage).where(st.c.claim_id == claim_id, ~st.c.status.in_(TERMINAL_OK))).scalars())
        immediate = bool(set(failed) & {'claim_rules', 'supplytrace'})
        con.execute(update(st).where(st.c.claim_id == claim_id, st.c.stage.in_(failed)).values(status='RETRYING', attempts=0, error=None))
        con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).values(analysis_status='RETRYING' if immediate else 'ENRICH_RETRY', attempts=0, next_attempt_at=None, error=None))
        s = con.execute(select(m.stream_sessions.c.status).where(m.stream_sessions.c.session_id == row['session_id'])).scalar()
        emit(con, [event(row['session_id'], 'retry_requested', f'{claim_id} retry requested for stage(s) {", ".join(failed)}.' + ('' if s in m.STREAM_ACTIVE else ' Session is no longer active; the runner retries claims of finished sessions too.'), claim_id=claim_id)])
    return claim_coverage(claim_id)

def phoenix_min_claims():
    from app.services.phoenix import CONFIG
    return CONFIG['min_successor_claims']

def phoenix_threshold():
    from app.services.phoenix import CONFIG
    return CONFIG['score_threshold']
