"""Unit tests for Next-Best-Evidence, Member Radar and Phoenix (no database)."""
import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.services import radar, phoenix
from app.services.cases import rank_values, apply_outcome
from app.services.evidence import CATALOG, beta_smoothed, decision_metrics, ranked, position, notice_text

TAXONOMY = {'duplicate_billing', 'impossible_timing', 'missing_encounter_indicator', 'upcoding_indicator', 'unbundling_indicator', 'early_refill', 'excessive_utilization', 'consumable_quantity_anomaly', 'supply_unit_price_anomaly', 'duplicate_supply_charge', 'estimate_to_final_drift', 'provider_statistical_anomaly', 'corroborated_referral_concentration', 'stolen_id_batch', 'member_id_possibly_compromised', 'phoenix_successor'}

# ---------- Feature 1: Next-Best-Evidence ----------
def test_catalog_matches_existing_taxonomy():
    assert set(CATALOG['by_finding_type']) == TAXONOMY
    for entries in CATALOG['by_finding_type'].values():
        for e in entries:
            assert e['evidence_type'] in CATALOG['evidence_types'] and 0 < e['p_benign'] < 1 and e['estimated_days'] > 0

@pytest.mark.parametrize('benign,total', [(0, 0), (0, 50), (50, 50), (3, 7)])
def test_beta_smoothing_strictly_inside_unit_interval(benign, total):
    assert 0 < beta_smoothed(benign, total) < 1

def finding(fid, severity='HIGH', status='ACTIVE', claims=('C1',), engine='rules', ftype='duplicate_billing', completeness=1.0):
    return dict(finding_id=fid, finding_type=ftype, severity=severity, status=status, claim_ids=list(claims), engine=engine, data_completeness=completeness)

def claim(cid, paid=1000, member='M1'):
    return dict(claim_id=cid, claim_status='paid', paid_amount_usd=paid, allowed_amount_usd=paid, member_id=member)

def test_resolving_only_active_finding_removes_all_risk():
    fs = [finding('F1')]
    assert rank_values(fs, [claim('C1')], .2)['priority_score'] > 0
    assert rank_values(apply_outcome(fs, 'F1', 'explains'), [claim('C1')], .2)['priority_score'] == 0

def test_corroboration_follows_scoring_policy_and_preserves_input():
    fs = [finding('F1', 'MEDIUM', completeness=.5), finding('F2', 'MEDIUM', claims=('C2',))]
    original = copy.deepcopy(fs)
    after = apply_outcome(fs, 'F1', 'supports')
    assert fs == original
    assert after[0]['status'] == 'ESCALATED' and after[0]['data_completeness'] == 1.0 and after[0]['severity'] == 'MEDIUM'
    cs = [claim('C1'), claim('C2')]
    assert rank_values(after, cs, 0)['priority_score'] > rank_values(fs, cs, 0)['priority_score']
    assert after[1] == fs[1]

@pytest.mark.parametrize('denials,severity', [(1, 'MEDIUM'), (2, 'MEDIUM'), (3, 'HIGH'), (5, 'HIGH')])
def test_three_distinct_denials_raise_batch_severity(denials, severity):
    fs = [finding('B', 'MEDIUM', ftype='stolen_id_batch')]
    assert apply_outcome(fs, 'B', 'supports', denials)[0]['severity'] == severity

def test_queue_tie_handling_is_deterministic_and_excludes_zero_scores():
    scores = [('CASE-B', 50.0), ('CASE-A', 50.0), ('CASE-C', 0.0), ('CASE-D', 70.0)]
    order = ranked(scores, 'CASE-X', 50.0, 'NEW')
    assert order == ['CASE-D', 'CASE-A', 'CASE-B', 'CASE-X']
    assert position(ranked(scores, 'CASE-X', 0, 'NEW'), 'CASE-X') is None
    assert position(ranked(scores, 'CASE-X', 99, 'CLOSED'), 'CASE-X') is None

def test_capacity_changes_decision_relevance():
    scores = [('A', 80.0), ('B', 60.0), ('C', 40.0)]
    def state(score, k):
        pos = position(ranked(scores, 'X', score, 'NEW'), 'X'); return {'priority': score, 'in_capacity': pos is not None and pos <= k}
    for k, expected in [(1, False), (3, True)]:
        m = decision_metrics(.5, state(55, k), state(35, k), state(65, k), 3)
        assert m['crosses_capacity_boundary'] is expected

def test_evidence_valuable_even_when_expected_delta_is_negative():
    """Likely-benign evidence that would drop a case out of capacity has a negative signed delta but real decision value."""
    base = {'priority': 60, 'in_capacity': True}; clear = {'priority': 20, 'in_capacity': False}; confirm = {'priority': 62, 'in_capacity': True}
    m = decision_metrics(.8, base, clear, confirm, 2)
    assert m['expected_directed_delta'] > 0  # (sign convention: clearing reduces the score)
    m = decision_metrics(.8, base, {'priority': 60, 'in_capacity': True}, {'priority': 40, 'in_capacity': False}, 2)
    assert m['expected_directed_delta'] < 0 and m['legacy_voi'] == 0 and m['decision_value'] > 0 and m['decision_change_probability'] == pytest.approx(.2)

def test_cheaper_evidence_ranks_higher_for_equal_impact():
    base = {'priority': 60, 'in_capacity': True}; clear = {'priority': 30, 'in_capacity': False}
    assert decision_metrics(.5, base, clear, base, 1)['decision_value'] > decision_metrics(.5, base, clear, base, 7)['decision_value']

def test_notice_is_simulated_specific_and_safe():
    text = notice_text({'primary_code': 'SIM-DME-CATHETER', 'service_date': date(2026, 9, 2)}, {'provider_name': 'Synthetic Provider 252'})
    assert text.startswith('SIMULATED NOTICE') and 'September 02, 2026' in text and 'Synthetic Provider 252' in text and 'catheter' in text
    assert 'never ask you for passwords' in text and 'fraud' not in text.lower()

# ---------- Feature 2: Member Radar ----------
AS_OF = date(2026, 9, 30)

def mini_dataset(new_members=30, referred=0, with_profiles=True, context=False):
    d = {'claims': [], 'referrals': [], 'encounters': [], 'providers': [dict(provider_id=p, facility_id='F' + p) for p in ['PB', 'PX']], 'member_profiles': [], 'provider_profiles': []}
    n = 0
    def add(member, provider, day, code='SIM-DME-CATHETER', encounter=True):
        nonlocal n; n += 1
        d['claims'].append(dict(claim_id=f'C{n:05d}', member_id=member, provider_id=provider, facility_id='F' + provider, encounter_id=f'E{n}' if encounter else None, service_date=day, primary_code=code))
    # Quiet baseline: one new member per month at PB; background members at PX across the period.
    for i in range(10): add(f'BASE{i}', 'PB', date(2025, 10, 5) + timedelta(days=30 * i))
    for i in range(60): add(f'M{i:03d}', 'PX', date(2025, 8, 1) + timedelta(days=i * 5), code='SIM-VISIT')
    for i in range(new_members):
        member = f'M{i:03d}'; day = AS_OF - timedelta(days=i % 20)
        if context: add(member, 'PX', day - timedelta(days=200), code='SIM-DME-CATHETER')
        if i < referred: d['referrals'].append(dict(member_id=member, destination_provider_id='PB', source_provider_id='PX', referral_date=day - timedelta(days=5)))
        add(member, 'PB', day, encounter=False)
    if with_profiles:
        d['provider_profiles'] = [dict(provider_id='PB', latitude=39.96, longitude=-82.99, enrollment_date=date(2025, 10, 1)), dict(provider_id='PX', latitude=39.96, longitude=-82.99, enrollment_date=date(2024, 1, 1))]
        d['member_profiles'] = [dict(member_id=f'M{i:03d}', latitude=30.0 + i % 10, longitude=-100 + i % 7) for i in range(60)] + [dict(member_id=f'BASE{i}', latitude=39.9, longitude=-83.0) for i in range(10)]
    return d

def test_haversine_known_distance():
    assert radar.haversine_km((39.9612, -82.9988), (41.8781, -87.6298)) == pytest.approx(447, abs=10)
    assert radar.haversine_km(None, (1, 1)) is None

def test_window_boundaries_and_first_contact_burst():
    d = mini_dataset(); ix = radar.Index(d)
    assert ix.as_of == AS_OF and ix.recent_start == AS_OF - timedelta(days=90) and ix.baseline_start == ix.recent_start - timedelta(days=365)
    s = radar.member_signals(ix, 'M000')
    assert s['first_contact_burst']['value'] == 1.0
    assert s['missing_encounter_rate']['value'] == 1.0

def test_cross_provider_duplicate_signal():
    d = mini_dataset(new_members=1)
    d['claims'].append(dict(claim_id='CDUP', member_id='M000', provider_id='PX', facility_id='FPX', encounter_id='E', service_date=AS_OF - timedelta(days=3), primary_code='SIM-DME-CATHETER'))
    s = radar.member_signals(radar.Index(d), 'M000')
    assert s['cross_provider_duplicates']['value'] >= 1

def test_geography_degrades_without_profiles():
    ix = radar.Index(mini_dataset(with_profiles=False))
    s = radar.member_signals(ix, 'M000')
    assert s['geographic_jump_km']['value'] is None and 'unavailable' in s['geographic_jump_km']['coverage']
    batch = radar.provider_batches(ix)['PB']
    geo = next(c for c in batch['checks'] if c['name'] == 'geographic_dispersion_percentile')
    assert geo['evaluable'] is False and batch['qualified'] is True

def test_robust_z_uses_minimum_scale_for_zero_variance():
    z = radar.robust_z({'a': 0.0, 'b': 0.0, 'c': 0.0, 'd': 3.0}, 1.0)
    assert z['a'] == 0 and z['d'] == 3.0

def test_suspicious_batch_detected():
    batch = radar.provider_batches(radar.Index(mini_dataset()))['PB']
    assert batch['qualified'] and batch['metrics']['new_members'] == 30 and batch['metrics']['share_without_prior_relationship'] == 1.0

def test_referred_high_growth_not_flagged():
    batch = radar.provider_batches(radar.Index(mini_dataset(referred=30)))['PB']
    assert not batch['qualified'] and batch['metrics']['share_without_prior_relationship'] == 0
    assert batch['metrics']['share_with_prior_care_context'] == 1.0

def test_prior_care_context_blocks_flag():
    assert not radar.provider_batches(radar.Index(mini_dataset(context=True)))['PB']['qualified']

def test_prior_relationship_ignores_records_created_during_burst():
    d = mini_dataset(new_members=30); first = AS_OF
    d['encounters'].append(dict(member_id='M000', provider_id='PB', service_start=datetime.combine(first + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)))
    ix = radar.Index(d)
    assert ix.prior_relationship('M000', 'PB', first) is None

def test_analyze_creates_bounded_batch_finding():
    outputs, members, batches, batch_members = radar.analyze(mini_dataset())
    kinds = [f.finding_type for f, _ in outputs]
    assert kinds.count('stolen_id_batch') == 1
    f, ev = next(x for x in outputs if x[0].finding_type == 'stolen_id_batch')
    assert f.entity_id == 'PB' and f.severity == 'MEDIUM' and len(f.related_claim_ids) == 30 and len(ev) <= 22
    assert {b['provider_id'] for b in batches if b['qualified']} == {'PB'} and len([m for m in batch_members if m['provider_id'] == 'PB']) == 30

# ---------- Feature 3: Phoenix ----------
def phoenix_dataset(days_after=25, share_identifiers=True, overlap=True, same_address=False):
    d = {'claims': [], 'referrals': [], 'investigation_history': [], 'relationships': [], 'provider_profiles': []}
    status_day = date(2026, 3, 1); start = status_day + timedelta(days=days_after); n = 0
    def add(p, m, day, code):
        nonlocal n; n += 1; d['claims'].append(dict(claim_id=f'C{n}', provider_id=p, facility_id='F' + p, member_id=m, service_date=day, primary_code=code))
    for i in range(20): add('A', f'M{i}', status_day - timedelta(days=10 + i), 'SIM-DME-SUPPLY')
    for i in range(15): add('B', f'M{i}' if overlap else f'N{i}', start + timedelta(days=5 + i), 'SIM-DME-SUPPLY' if overlap else 'SIM-LAB-PANEL')
    for src in ['R1', 'R2']:
        d['referrals'].append(dict(referral_id=f'RA{src}', source_provider_id=src, destination_provider_id='A', referral_date=status_day - timedelta(days=60)))
        if overlap: d['referrals'].append(dict(referral_id=f'RB{src}', source_provider_id=src, destination_provider_id='B', referral_date=start + timedelta(days=2)))
    common = dict(practice_owner_id='OWN', bank_token='SYNTH-BANK-TOKEN-X', phone='SYN-555-1')
    d['provider_profiles'] = [dict(provider_id='A', operating_status='revoked', status_effective_date=status_day, enrollment_date=date(2025, 10, 1), address='10 Main Street, Columbus', **common),
                              dict(provider_id='B', operating_status='active', status_effective_date=None, enrollment_date=start, address='10 Main Street, Columbus' if same_address else '99 Other Road, Columbus', **(common if share_identifiers else dict(practice_owner_id='OWN2', bank_token='SYNTH-BANK-TOKEN-Y', phone='SYN-555-2')))]
    return d

def test_obvious_successor_flagged_with_components():
    outputs, links = phoenix.analyze(phoenix_dataset())
    link = links[0]
    assert link['flagged'] and set(link['components']) == set(phoenix.COMPONENTS)
    assert set(link['details']['matched_identifiers']) == {'practice_owner_id', 'bank_token', 'phone'}
    assert outputs[0][0].finding_type == 'phoenix_successor' and outputs[0][0].entity_id == 'B'

def test_subtle_successor_flagged_without_identifiers():
    outputs, links = phoenix.analyze(phoenix_dataset(share_identifiers=False))
    assert links[0]['flagged'] and links[0]['components']['shared_identifiers'] == 0 and len(outputs) == 1

def test_address_only_lookalike_not_flagged():
    outputs, links = phoenix.analyze(phoenix_dataset(share_identifiers=False, overlap=False, same_address=True))
    assert links[0]['details']['matched_identifiers'].keys() == {'address'} and not links[0]['flagged'] and not outputs

def test_outside_eligibility_window_excluded():
    assert phoenix.analyze(phoenix_dataset(days_after=200)) == ([], [])

def test_missing_identifiers_never_match():
    score, matches, compared = phoenix.identifier_matches({'phone': None, 'bank_token': ''}, {'phone': None, 'bank_token': ''})
    assert score == 0 and not matches and not compared

def test_neighbouring_house_numbers_are_not_one_address():
    assert phoenix.address_similarity('471 Synthetic Medical Way, Houston', '478 Synthetic Medical Way, Houston') == 0
    assert phoenix.address_similarity('412 Synthetic Health Plaza', '412 Synthetic Health Plaza') == 1

def test_empty_sets_are_unavailable_not_identical():
    assert phoenix.jaccard(set(), set()) is None and phoenix.cosine({}, {'a': 1}) is None

def test_successor_finding_does_not_inherit_predecessor_findings():
    outputs, _ = phoenix.analyze(phoenix_dataset())
    f, ev = outputs[0]
    assert set(f.related_provider_ids) == {'B'}
    assert not any(e.source_table_or_type == 'findings' for e in ev)
