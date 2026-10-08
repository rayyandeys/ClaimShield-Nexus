"""Live stream scenario pack v1: deterministic synthetic claims for live claims monitoring.

Every session gets its own provider cohort (three new synthetic providers) and its own brand-new members, so replays never
collide with earlier sessions or with the original scenario records (P0251-P0264 are never used). Ordinary claims go to
existing synthetic members who already have prior claims for the same service code (genuine care context); the
new-member burst at the DME supplier uses brand-new members with no history.

Scenario labels are stored for the demo display only. No detection engine reads them: every finding comes from the
existing engines evaluating the stored records. Service dates replay September 2026 (inside the dataset timeline and the
synthetic billing-policy window); the ingestion timestamp is the real time ClaimShield received the claim.
"""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

PACK_VERSION = 'live-stream-pack-v1'
DEFAULT_SEED = 20261010
MODES = {
    'mixed_demo': 'Mostly ordinary claims plus a duplicate submission, a supply-quantity outlier, an overlapping appointment and a new-member burst at one DME supplier.',
    'normal_only': 'Ordinary claims only (throughput and false-positive check).',
}
FIRST_DAY, LAST_DAY = date(2026, 9, 1), date(2026, 9, 29)
COHORT_START = date(2026, 8, 24)
SUBMIT_CAP = date(2026, 9, 30)
STATE, CITY, LATLON = 'WA', 'Seattle', (47.6062, -122.3321)
# role: (specialty, provider/claim type, facility, codes with base price, first appointment hour, slot minutes, duration)
ROLES = {
    'A': ('Primary Care', 'professional', 'F0028', [('SIM-VISIT', 120), ('SIM-CONSULT-L2', 170), ('SIM-CONSULT-L1', 110)], 8, 45, 30),
    'B': ('Surgery', 'facility', 'F0011', [('SIM-SURGERY-BASIC', 6000)], 7, 150, 120),
    'C': ('DME Supplier', 'dme', 'F0058', [('SIM-DME-SUPPLY', 150), ('SIM-DME-CATHETER', 165)], 9, 25, 20),
}
SUPPLIES = {'GLOVE': ('Gloves', 3.84, (10, 20)), 'BANDAGE': ('Bandages', 8.73, (10, 20)), 'SYRINGE': ('Syringes', 2.20, (2, 4))}
OUTLIER_QUANTITY = 160
BATCH_TARGET = 30
# The first twelve arrivals show every scenario quickly; references are 1-based sequence numbers.
SHOWCASE = [('normal', 'A', None), ('new_member_burst', 'C', None), ('normal_supplies', 'B', None), ('normal', 'A', None),
            ('duplicate_submission', 'A', 1), ('new_member_burst', 'C', None), ('normal_supplies', 'B', None),
            ('supply_quantity_outlier', 'B', None), ('new_member_burst', 'C', None), ('normal', 'A', None),
            ('overlapping_appointment', 'A', 10), ('new_member_burst', 'C', None)]
SCENARIO_LABELS = {
    'normal': 'Ordinary office visit', 'normal_supplies': 'Ordinary procedure with itemized supplies',
    'new_member_burst': 'Ordinary-looking DME claim for a previously unseen member',
    'duplicate_submission': 'Second submission of an earlier claim', 'supply_quantity_outlier': 'Procedure with an unusual supply quantity',
    'overlapping_appointment': 'Appointment overlapping another member\'s appointment',
}

def money(value):
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def validate_config(rate, count, seed, mode, max_count):
    errors = []
    if not 0.2 <= rate <= 5: errors.append('rate_per_second must be between 0.2 and 5 claims per second')
    if not 1 <= count <= max_count: errors.append(f'max_claims must be between 1 and {max_count}')
    if not 0 <= seed < 2 ** 31: errors.append('seed must be a non-negative 32-bit integer')
    if mode not in MODES: errors.append(f'scenario_mode must be one of {sorted(MODES)}')
    if errors: raise ValueError('; '.join(errors))

def ids(session_number):
    k = f'{session_number:04d}'
    return {'providers': {role: f'PS{k}{role}' for role in ROLES}, 'claim': lambda seq: f'SC{k}-{seq:04d}', 'encounter': lambda seq: f'SE{k}-{seq:04d}',
            'line': lambda seq, i: f'SL{k}-{seq:04d}-{i}', 'supply': lambda seq, i: f'SS{k}-{seq:04d}-{i}', 'member': lambda n: f'MS{k}{n:03d}', 'relationship': lambda role: f'RS{k}{role}'}

def roles_needed(count, mode):
    """How many context members per role and brand-new members the plan needs (computed before choosing members)."""
    skeleton = plan_skeleton(count, mode)
    return {'A': sum(1 for s, r, ref in skeleton if r == 'A' and s != 'duplicate_submission'), 'B': sum(1 for s, r, _ in skeleton if r == 'B'), 'new': sum(1 for s, r, _ in skeleton if r == 'C')}

def plan_skeleton(count, mode):
    if mode == 'normal_only':
        return [('normal', 'A', None) if i % 2 == 0 else ('normal_supplies', 'B', None) for i in range(count)]
    out = list(SHOWCASE[:count]); burst = sum(1 for s in out if s[0] == 'new_member_burst')
    while len(out) < count:
        position = len(out)
        if burst < BATCH_TARGET and position % 3 != 2: out.append(('new_member_burst', 'C', None)); burst += 1
        else: out.append(('normal', 'A', None) if (position // 3) % 2 == 0 else ('normal_supplies', 'B', None))
    return out

def cohort(session_number, seed, count, mode, context_pools):
    """Cohort records for a session. context_pools: {'A': [(member_id, code), ...], 'B': [...]}: existing members with a
    prior claim for that code, already rotated for this session. Returns (rows by table, cohort description)."""
    rng = random.Random(f'{seed}:cohort:{session_number}'); x = ids(session_number); need = roles_needed(count, mode)
    rows = {'providers': [], 'members': [], 'relationships': [], 'provider_profiles': [], 'entities': []}
    for role, (specialty, ptype, facility, *_rest) in ROLES.items():
        pid = x['providers'][role]
        rows['providers'].append(dict(provider_id=pid, provider_name=f'Stream Demo Provider {session_number:04d}{role}', provider_type=ptype, specialty=specialty, facility_id=facility, state=STATE, active_from=COHORT_START, synthetic_identity='yes'))
        rows['relationships'].append(dict(relationship_id=x['relationship'](role), source_id=pid, source_type='provider', target_id=facility, target_type='facility', relationship_type='affiliated_with', effective_from=COHORT_START, effective_to=None, record_source='synthetic_provider_directory', verification_status='recorded'))
        rows['provider_profiles'].append(dict(provider_id=pid, address=f'{900 + session_number} Stream Demo Way Suite {role}, {CITY}', zip=f'SYN-STR{session_number:04d}', latitude=round(LATLON[0] + rng.uniform(-.03, .03), 5), longitude=round(LATLON[1] + rng.uniform(-.03, .03), 5), phone=f'SYN-555-8{session_number:04d}{ord(role)}', practice_owner_id=f'PO-STREAM-{session_number:04d}{role}', bank_token=f'SYNTH-BANK-STREAM-{session_number:04d}{role}', operating_status='active', status_effective_date=None, enrollment_date=COHORT_START, profile_source=PACK_VERSION))
        rows['entities'].append(dict(entity_id=pid, entity_type='provider', label=f'Stream Demo Provider {session_number:04d}{role}', source_table='providers'))
    new_members = [x['member'](n) for n in range(1, need['new'] + 1)]
    for mid in new_members:
        rows['members'].append(dict(member_id=mid, age_band=rng.choice(['18-29', '30-44', '45-64', '65-74', '75+']), plan_type=rng.choice(['Medicaid', 'Medicaid', 'Medicare', 'Commercial']), state=STATE, city=CITY, complexity_band='low', synthetic_identity='yes'))
        rows['entities'].append(dict(entity_id=mid, entity_type='member', label=mid, source_table='members'))
    for role in ['A', 'B']:
        if len(context_pools.get(role, [])) < need[role]: raise ValueError(f'Not enough existing members with care context for role {role}: need {need[role]}')
    description = {'pack_version': PACK_VERSION, 'session_number': session_number, 'providers': x['providers'], 'roles': {role: {'specialty': v[0], 'claim_type': v[1], 'facility_id': v[2]} for role, v in ROLES.items()},
                   'new_members': new_members, 'context_members': {role: [list(p) for p in context_pools[role][:need[role]]] for role in ['A', 'B']}, 'city': CITY, 'service_window': [FIRST_DAY, LAST_DAY], 'enrollment_date': COHORT_START}
    return rows, description

def plan(seed, count, mode, description):
    """Deterministic arrival plan: one item per sequence number."""
    skeleton = plan_skeleton(count, mode); used = {'A': 0, 'B': 0, 'new': 0}; items = []; slots = {}
    span = (LAST_DAY - FIRST_DAY).days
    for seq, (scenario, role, ref) in enumerate(skeleton, 1):
        item = {'seq': seq, 'scenario': scenario, 'role': role, 'ref': ref}
        if ref:
            target = items[ref - 1]; item['day'] = target['day']
            if scenario == 'duplicate_submission': item.update(member_id=target['member_id'], code=target['code'], start=target['start'])
            else:
                member, code = description['context_members']['A'][used['A']]; used['A'] += 1
                item.update(member_id=member, code=code, start=target['start'] + timedelta(minutes=10))
        else:
            item['day'] = FIRST_DAY + timedelta(days=(seq - 1) * span // max(count - 1, 1))
            if role == 'C': item['member_id'] = description['new_members'][used['new']]; used['new'] += 1; item['code'] = ROLES['C'][3][(seq // 2) % 2][0]
            else: member, code = description['context_members'][role][used[role]]; used[role] += 1; item.update(member_id=member, code=code)
            _, _, _, _, hour, slot_minutes, _ = ROLES[role]; slot = slots.get((role, item['day']), 0); slots[(role, item['day'])] = slot + 1
            item['start'] = datetime.combine(item['day'], datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=hour, minutes=slot_minutes * slot)
        items.append(item)
    return items

def bundle(item, items, session_number, seed):
    """Source records for one arrival (claim, lines, encounter, supply items), typed like imported rows."""
    x = ids(session_number); seq = item['seq']; role = item['role']; specialty, ctype, facility, codes, _, _, duration = ROLES[role]
    if item['scenario'] == 'duplicate_submission':
        original = bundle(items[item['ref'] - 1], items, session_number, seed)
        claim = dict(original['claims'][0], claim_id=x['claim'](seq), submitted_date=min(original['claims'][0]['submitted_date'] + timedelta(days=1), SUBMIT_CAP))
        lines = [dict(l, claim_line_id=x['line'](seq, i), claim_id=claim['claim_id']) for i, l in enumerate(original['claim_lines'], 1)]
        return {'claims': [claim], 'claim_lines': lines, 'encounters': [], 'supply_items': [], 'claim_estimates': []}
    rng = random.Random(f'{seed}:{session_number}:{seq}'); pid = x['providers'][role]; cid = x['claim'](seq); eid = x['encounter'](seq)
    base = dict(codes)[item['code']]; lines = []; supplies = []
    if role == 'B':
        service = money(base * rng.uniform(.92, 1.15)); lines.append(dict(claim_line_id=x['line'](seq, 1), claim_id=cid, procedure_code=item['code'], unit_count=1, unit_charge_usd=service, line_billed_usd=service, modifier=None, days_supply=None, days_since_last_fill=None, line_role='service'))
        for i, code in enumerate(['GLOVE', 'BANDAGE', 'SYRINGE'], 2):
            label, price, (low, high) = SUPPLIES[code]
            quantity = OUTLIER_QUANTITY if item['scenario'] == 'supply_quantity_outlier' and code == 'BANDAGE' else rng.randint(low, high)
            unit = money(price * rng.uniform(.95, 1.08)); total = money(unit * quantity)
            lines.append(dict(claim_line_id=x['line'](seq, i), claim_id=cid, procedure_code=f'SIM-SUP-{code}', unit_count=quantity, unit_charge_usd=unit, line_billed_usd=total, modifier=None, days_supply=None, days_since_last_fill=None, line_role='consumable'))
            supplies.append(dict(supply_id=x['supply'](seq, i - 1), claim_id=cid, claim_line_id=x['line'](seq, i), supply_code=code, supply_description=label, quantity=quantity, unit_charge_usd=unit, line_billed_usd=total, billing_basis='synthetic_itemized_charge'))
    else:
        units = rng.randint(1, 3) if role == 'C' else 1; unit = money(base * rng.uniform(.9, 1.15)); total = money(unit * units)
        lines.append(dict(claim_line_id=x['line'](seq, 1), claim_id=cid, procedure_code=item['code'], unit_count=units, unit_charge_usd=unit, line_billed_usd=total, modifier=None, days_supply=None, days_since_last_fill=None, line_role='service'))
    billed = sum((l['line_billed_usd'] for l in lines), Decimal('0')); allowed = money(float(billed) * rng.uniform(.6, .7))
    claim = dict(claim_id=cid, member_id=item['member_id'], provider_id=pid, facility_id=facility, encounter_id=eid, service_date=item['day'], service_start=item['start'], service_duration_min=duration, claim_type=ctype, claim_status='pending', submitted_date=min(item['day'] + timedelta(days=rng.randint(1, 3)), SUBMIT_CAP), payment_date=None, primary_code=item['code'], correction_of_claim_id=None, related_claim_id=None, complexity_band='low', billed_amount_usd=billed, allowed_amount_usd=allowed, paid_amount_usd=Decimal('0.00'), member_responsibility_usd=Decimal('0.00'))
    encounter = dict(encounter_id=eid, member_id=item['member_id'], provider_id=pid, facility_id=facility, service_start=item['start'], duration_min=duration, documented_code=item['code'], documented_complexity='low', encounter_source='synthetic_scheduling_record')
    return {'claims': [claim], 'claim_lines': lines, 'encounters': [encounter], 'supply_items': supplies, 'claim_estimates': []}
