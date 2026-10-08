"""Read-only source inventory; writes derived documentation and an audit artifact."""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = {}
lines = ['# Discovered source schemas', '', 'Source CSVs are preserved unchanged. Amounts are USD. Empty fields are nullable.', '']
for path in sorted((ROOT / 'data').rglob('*.csv')):
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = reader.fieldnames
    details = {'rows': len(rows), 'columns': columns, 'duplicate_ids': len(rows) - len({r[columns[0]] for r in rows}), 'missing': {c: sum(not r[c] for r in rows) for c in columns}}
    for c in columns:
        if c in ['claim_type', 'claim_status', 'primary_code', 'complexity_band', 'documented_code', 'documented_complexity', 'line_role', 'modifier', 'relationship_type', 'source_type', 'target_type', 'scenario_type', 'outcome']:
            details[c] = dict(Counter(r[c] for r in rows))
    if 'service_date' in columns:
        details['date_range'] = [min(r['service_date'] for r in rows), max(r['service_date'] for r in rows)]
    report[str(path.relative_to(ROOT))] = details
    lines += [f'## {path.name} — {len(rows):,} rows', '', ', '.join(f'`{c}`' for c in columns), '']
(ROOT / 'docs').mkdir(exist_ok=True)
(ROOT / 'artifacts/evaluation').mkdir(parents=True, exist_ok=True)
(ROOT / 'docs/data_dictionary.md').write_text('\n'.join(lines), encoding='utf-8')
(ROOT / 'artifacts/evaluation/source_inventory.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
