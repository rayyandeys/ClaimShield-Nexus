"""Live stream generator unit tests (no database): validity, determinism, scenarios and configuration limits."""
from datetime import timedelta
from decimal import Decimal
import pytest
from app.services import stream_generator as gen
from app.services.ingestion import claim_issues, supply_issues
from app.services.detection import rules, supply_analysis, utilization_observations, utilization_thresholds

POOLS = {'A': [(f'M9{i:05d}', 'SIM-VISIT') for i in range(400)], 'B': [(f'M8{i:05d}', 'SIM-SURGERY-BASIC') for i in range(400)]}

def build(count=60, mode='mixed_demo', seed=gen.DEFAULT_SEED, session=7):
    rows, description = gen.cohort(session, seed, count, mode, POOLS)
    items = gen.plan(seed, count, mode, description)
    return rows, description, items, [gen.bundle(i, items, session, seed) for i in items]

def test_bundles_are_valid_like_imported_rows():
    _, _, items, bundles = build()
    for b in bundles:
        claim = b['claims'][0]; lines = b['claim_lines']
        encounter = b['encounters'][0] if b['encounters'] else next(x['encounters'][0] for x in bundles if x['encounters'] and x['encounters'][0]['encounter_id'] == claim['encounter_id'])
        errors = [i for i in claim_issues(claim, sum((l['line_billed_usd'] for l in lines), Decimal(0)), encounter) if i[2] == 'ERROR']
        assert not errors, (claim['claim_id'], errors)
        assert all(l['claim_id'] == claim['claim_id'] for l in lines)
        line_map = {l['claim_line_id']: l for l in lines}
        for s in b['supply_items']: assert s['claim_line_id'] in line_map and not supply_issues(s, line_map[s['claim_line_id']])
        assert gen.FIRST_DAY <= claim['service_date'] <= gen.LAST_DAY and claim['submitted_date'] <= gen.SUBMIT_CAP and claim['claim_status'] == 'pending'

def test_unique_identifiers_and_session_specific_ids():
    _, _, _, a = build(session=7); _, _, _, b = build(session=8)
    ids = lambda bs, t, k: [r[k] for x in bs for r in x[t]]
    for t, k in [('claims', 'claim_id'), ('claim_lines', 'claim_line_id'), ('encounters', 'encounter_id'), ('supply_items', 'supply_id')]:
        assert len(set(ids(a, t, k))) == len(ids(a, t, k))
        assert not set(ids(a, t, k)) & set(ids(b, t, k))

def test_cohort_references_are_consistent():
    rows, description, items, bundles = build()
    providers = {p['provider_id'] for p in rows['providers']}; members = {m['member_id'] for m in rows['members']} | {m for pool in POOLS.values() for m, _ in pool}
    assert providers == set(description['providers'].values()) and all(p.startswith('PS0007') for p in providers)
    assert {r['source_id'] for r in rows['relationships']} == providers and {p['provider_id'] for p in rows['provider_profiles']} == providers
    for b in bundles:
        c = b['claims'][0]; assert c['provider_id'] in providers and c['member_id'] in members
        for e in b['encounters']: assert (e['member_id'], e['provider_id'], e['facility_id']) == (c['member_id'], c['provider_id'], c['facility_id'])

def test_deterministic_generation():
    assert build()[3] == build()[3]
    assert build(seed=1)[3] != build(seed=2)[3]

def test_scenario_mix_and_maximum_count():
    _, _, items, _ = build(60)
    kinds = [i['scenario'] for i in items]
    assert len(items) == 60 and kinds.count('duplicate_submission') == 1 and kinds.count('supply_quantity_outlier') == 1 and kinds.count('overlapping_appointment') == 1
    assert kinds.count('new_member_burst') == gen.BATCH_TARGET and kinds.count('normal') + kinds.count('normal_supplies') > 20
    assert len(build(5)[2]) == 5 and len(build(500)[2]) == 500
    assert {i['scenario'] for i in build(40, 'normal_only')[2]} == {'normal', 'normal_supplies'}

def test_duplicate_scenario_satisfies_the_existing_duplicate_rule(dataset):
    _, _, items, bundles = build()
    dup = next(i for i in items if i['scenario'] == 'duplicate_submission'); original = bundles[dup['ref'] - 1]; copy = bundles[dup['seq'] - 1]
    a, b = original['claims'][0], copy['claims'][0]
    assert (a['member_id'], a['provider_id'], a['service_date'], a['primary_code'], a['service_start']) == (b['member_id'], b['provider_id'], b['service_date'], b['primary_code'], b['service_start']) and a['claim_id'] != b['claim_id']
    data = dict(dataset, claims=[a, b], encounters=original['encounters'], claim_lines=original['claim_lines'] + copy['claim_lines'])
    data['billing_policies'] = [dict(p, applicable_claim_types='professional', effective_to=gen.LAST_DAY) for p in data['billing_policies']]
    found = [f for f, _ in rules(data, {}) if f.finding_type == 'duplicate_billing']
    assert len(found) == 1 and set(found[0].related_claim_ids) == {a['claim_id'], b['claim_id']}

def test_overlap_scenario_satisfies_the_timing_rule(dataset):
    _, _, items, bundles = build()
    item = next(i for i in items if i['scenario'] == 'overlapping_appointment'); target = bundles[item['ref'] - 1]; own = bundles[item['seq'] - 1]
    data = dict(dataset, claims=[target['claims'][0], own['claims'][0]], encounters=target['encounters'] + own['encounters'], claim_lines=target['claim_lines'] + own['claim_lines'])
    data['billing_policies'] = [dict(p, applicable_claim_types='professional', effective_to=gen.LAST_DAY) for p in data['billing_policies']]
    assert [f.finding_type for f, _ in rules(data, {})] == ['impossible_timing']

def test_normal_claims_do_not_trigger_claim_rules(dataset):
    _, _, items, bundles = build()
    normal = [b for b, i in zip(bundles, items) if i['scenario'] in ('normal', 'normal_supplies', 'new_member_burst')]
    data = dict(dataset, claims=[b['claims'][0] for b in normal], encounters=[e for b in normal for e in b['encounters']], claim_lines=[l for b in normal for l in b['claim_lines']])
    data['billing_policies'] = [dict(p, applicable_claim_types='professional,facility,dme', effective_to=gen.LAST_DAY) for p in data['billing_policies']]
    _, peer_counts = utilization_observations(data['claims'])
    assert rules(data, utilization_thresholds(peer_counts)) == []

def test_supply_outlier_exceeds_matched_peer_threshold(dataset):
    _, _, items, bundles = build(200)
    supply = [b for b, i in zip(bundles, items) if i['role'] == 'B']
    data = dict(dataset, claims=[b['claims'][0] for b in supply], supply_items=[s for b in supply for s in b['supply_items']], claim_estimates=[])
    data['billing_policies'] = [dict(p, applicable_claim_types='facility', effective_to=gen.LAST_DAY) for p in data['billing_policies']]
    out, comparisons = supply_analysis(data)
    outlier = next(b for b, i in zip(bundles, items) if i['scenario'] == 'supply_quantity_outlier')
    flagged = [f for f, _ in out if f.finding_type == 'consumable_quantity_anomaly']
    assert [f.related_claim_ids for f in flagged] == [[outlier['claims'][0]['claim_id']]]

@pytest.mark.parametrize('rate,count,seed,mode', [(0, 60, 1, 'mixed_demo'), (10, 60, 1, 'mixed_demo'), (1, 0, 1, 'mixed_demo'), (1, 501, 1, 'mixed_demo'), (1, 60, -1, 'mixed_demo'), (1, 60, 1, 'chaos')])
def test_invalid_configuration_is_rejected(rate, count, seed, mode):
    with pytest.raises(ValueError): gen.validate_config(rate, count, seed, mode, 500)

def test_cohort_requires_enough_context_members():
    with pytest.raises(ValueError, match='care context'): gen.cohort(1, 1, 60, 'mixed_demo', {'A': POOLS['A'][:2], 'B': POOLS['B']})
