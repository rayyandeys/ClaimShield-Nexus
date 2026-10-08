"""Policy CLAIMSHIELD-SIU-V1 rules on controlled inputs (no database)."""
from app.services.policy import evaluate, readiness

def f(fid, ftype='duplicate_billing', engine='rules', status='ACTIVE'): return dict(finding_id=fid, finding_type=ftype, engine=engine, status=status)
def r(rid, fid, etype='corrected_claim_record', status='VERIFIED_SUPPORTS'): return dict(request_id=rid, finding_id=fid, evidence_type=etype, status=status)
OPEN = {'case_status': 'NEW', 'priority_score': 99.9}

def test_open_case_allows_evidence_requests_and_monitoring_only():
    ev = evaluate(OPEN, [f('F1')], [], [])
    assert ev['request_more_evidence']['status'] == 'ALLOWED' and ev['monitor_provider']['status'] == 'ALLOWED'
    assert ev['full_investigation']['status'] == 'NEEDS_EVIDENCE' and ev['external_referral']['status'] == 'NEEDS_EVIDENCE'

def test_high_priority_score_alone_authorizes_nothing():
    low, high = evaluate(dict(OPEN, priority_score=1), [f('F1')], [], []), evaluate(dict(OPEN, priority_score=100), [f('F1')], [], [])
    assert low == high and high['external_referral']['status'] != 'ALLOWED'

def test_unreviewed_ml_and_phoenix_findings_do_not_support_escalation():
    ev = evaluate(OPEN, [f('M', 'provider_statistical_anomaly', 'ml'), f('P', 'phoenix_successor', 'phoenix')], [], [])
    assert ev['full_investigation']['status'] == 'NEEDS_EVIDENCE' and '2 unreviewed ML/Phoenix' in ev['full_investigation']['reason']

def test_reviewed_supporting_evidence_makes_full_investigation_eligible():
    ev = evaluate(OPEN, [f('F1')], [r('ER1', 'F1')], [])
    assert ev['full_investigation']['status'] == 'ALLOWED' and ev['full_investigation']['evidence_refs'] == ['ER1']
    assert evaluate(OPEN, [f('F1', status='ESCALATED')], [], [])['full_investigation']['status'] == 'ALLOWED'
    # An open (unreviewed) request is not reviewed evidence.
    assert evaluate(OPEN, [f('F1')], [r('ER1', 'F1', status='RECEIVED')], [])['full_investigation']['status'] == 'NEEDS_EVIDENCE'

def test_explained_findings_cannot_justify_escalation_but_others_remain():
    explained = evaluate(OPEN, [f('P', 'phoenix_successor', 'phoenix', 'EXPLAINED')], [r('ER1', 'P', 'ownership_acquisition_records')], [])
    assert explained['full_investigation']['status'] == 'BLOCKED' and explained['request_more_evidence']['status'] == 'BLOCKED'
    mixed = evaluate(OPEN, [f('P', 'phoenix_successor', 'phoenix', 'EXPLAINED'), f('M', 'provider_statistical_anomaly', 'ml')], [r('ER1', 'P', 'ownership_acquisition_records')], [])
    assert mixed['full_investigation']['status'] == 'NEEDS_EVIDENCE' and '1 explained finding' in mixed['full_investigation']['reason']

def test_external_referral_lists_missing_requirements_and_never_is_allowed():
    fs = [f('B', 'stolen_id_batch', 'radar', 'ESCALATED'), f('P', 'phoenix_successor', 'phoenix')]
    reqs = [r('ER1', 'B', 'member_confirmation'), r('ER2', 'B', 'member_confirmation')]
    ev = evaluate(OPEN, fs, reqs, [])['external_referral']
    assert ev['status'] == 'NEEDS_EVIDENCE' and any('active investigation' in m for m in ev['missing']) and any('ownership acquisition records' in m for m in ev['missing'])
    reqs.append(r('ER3', 'P', 'ownership_acquisition_records'))
    ready = evaluate(dict(OPEN, case_status='UNDER_REVIEW'), fs, reqs, [])['external_referral']
    assert ready['status'] == 'REQUIRES_APPROVAL' and any('authorized supervisor' in m for m in ready['missing'])
    recorded = evaluate(OPEN, fs, reqs, [{'action': 'full_investigation', 'status': 'RECOMMENDED'}])['external_referral']
    assert recorded['status'] == 'REQUIRES_APPROVAL'
    # Only one reviewed supporting record (the ownership review explains rather than supports): not independent enough.
    one = [reqs[0], dict(reqs[2], status='VERIFIED_EXPLAINS')]
    assert evaluate(OPEN, fs, one, [{'action': 'full_investigation', 'status': 'RECOMMENDED'}])['external_referral']['status'] == 'NEEDS_EVIDENCE'

def test_closed_cases_block_every_action_and_readiness():
    closed = {'case_status': 'CLOSED'}; ev = evaluate(closed, [f('F1')], [r('ER1', 'F1')], [])
    assert {a['status'] for a in ev.values()} == {'BLOCKED'} and readiness(ev, closed, []) == 'NO_ACTION_ELIGIBLE'

def test_readiness_is_derived_from_policy_not_priority():
    assert readiness(evaluate(OPEN, [f('F1')], [], []), OPEN, []) == 'MORE_EVIDENCE_NEEDED'
    assert readiness(evaluate(OPEN, [f('F1')], [r('ER1', 'F1')], []), OPEN, []) == 'INVESTIGATION_REVIEW_ELIGIBLE'
    assert readiness(evaluate(OPEN, [f('F1')], [], []), OPEN, [{'action': 'monitor_provider', 'status': 'RECOMMENDED'}]) == 'MONITORING'
    assert readiness(evaluate(OPEN, [f('F1')], [], []), OPEN, [{'action': 'external_referral', 'status': 'PENDING_APPROVAL'}]) == 'APPROVAL_REQUIRED'
