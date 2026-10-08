"""Policy-governed decisions end to end on claimshield_features_test, after the feature suite (which records the P0252
member-confirmation reviews and resolves the P0258 acquisition). No evidence is fabricated here."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.core import engine, settings
from app import models as m
from app.main import app

pytestmark = pytest.mark.features
A = '/api/v1'

@pytest.fixture(scope='module')
def client():
    if settings.db_name != 'claimshield_features_test': pytest.skip('Set DB_NAME=claimshield_features_test after the feature suite.')
    with TestClient(app) as c: yield c

def case_for(client, provider):
    return next(l['case_id'] for l in client.get(A + f'/providers/{provider}/predecessors').json()['items'] if l['flagged'])

def actions(client, cid): return {a['action']: a for a in client.get(A + f'/cases/{cid}/policy').json()['actions']}

def snapshot():
    with engine.connect() as con:
        return (sorted(con.execute(select(m.findings.c.finding_id, m.findings.c.status, m.findings.c.severity)).all()), sorted(con.execute(select(m.cases.c.case_id, m.cases.c.priority_score, m.cases.c.case_status, m.cases.c.evidence_version)).all()), sorted(con.execute(select(m.evidence_requests.c.request_id, m.evidence_requests.c.status)).all()))

def test_p0252_policy_reflects_its_reviewed_evidence(client):
    cid = case_for(client, 'P0252'); a = actions(client, cid)
    assert a['request_more_evidence']['status'] == 'ALLOWED' and a['monitor_provider']['status'] == 'ALLOWED'
    # The batch finding was corroborated by reviewed member denials in the feature workflow.
    assert a['full_investigation']['status'] == 'ALLOWED' and a['full_investigation']['evidence_refs']
    ref = a['external_referral']; assert ref['status'] == 'NEEDS_EVIDENCE'
    assert any('ownership acquisition records' in x for x in ref['missing']) and any('active investigation' in x for x in ref['missing'])

def test_p0258_explained_phoenix_finding_does_not_support_escalation(client):
    cid = case_for(client, 'P0258'); p = client.get(A + f'/cases/{cid}/policy').json(); a = {x['action']: x for x in p['actions']}
    assert p['case']['explained_findings'] >= 1 and 'explained finding' in a['full_investigation']['reason']
    assert a['full_investigation']['status'] == 'NEEDS_EVIDENCE' and a['external_referral']['status'] != 'REQUIRES_APPROVAL'

def test_decisions_persist_are_enforced_and_audited_without_touching_case_data(client):
    cid = case_for(client, 'P0258'); before = snapshot()
    with engine.connect() as con: audit_before = con.execute(select(func.count()).select_from(m.audit).where(m.audit.c.case_id == cid)).scalar()
    assert client.post(A + f'/cases/{cid}/decisions', json={'action': 'monitor_provider', 'justification': 'short'}).status_code == 422
    blocked = client.post(A + f'/cases/{cid}/decisions', json={'action': 'full_investigation', 'justification': 'Attempt without reviewed supporting evidence.'}).json()
    assert blocked['status'] == 'BLOCKED' and blocked['policy_version'] == 'CLAIMSHIELD-SIU-V1'
    rec = client.post(A + f'/cases/{cid}/decisions', json={'action': 'monitor_provider', 'justification': 'Acquisition explained; keep the provider under monitoring.'}).json()
    assert rec['status'] == 'RECOMMENDED'
    p = client.get(A + f'/cases/{cid}/policy').json()
    assert [d['status'] for d in p['decisions'][:2]] == ['RECOMMENDED', 'BLOCKED'] and p['readiness'] == 'MONITORING'
    timeline = client.get(A + f'/cases/{cid}/timeline').json()['items']
    kinds = [t['action_type'] for t in timeline]
    assert {'policy_evaluation_completed', 'action_blocked_by_policy', 'investigation_action_recommended'} <= set(kinds)
    assert any('CLAIMSHIELD-SIU-V1' in t['explanation'] for t in timeline)
    with engine.connect() as con: assert con.execute(select(func.count()).select_from(m.audit).where(m.audit.c.case_id == cid)).scalar() == audit_before + 4
    assert snapshot() == before

def test_approval_cannot_be_bypassed(client):
    cid = case_for(client, 'P0252')
    # A full-investigation recommendation satisfies the "active investigation" requirement but not the missing ownership records.
    assert client.post(A + f'/cases/{cid}/decisions', json={'action': 'full_investigation', 'justification': 'Three reviewed member denials corroborate the batch.'}).json()['status'] == 'RECOMMENDED'
    ref = client.post(A + f'/cases/{cid}/decisions', json={'action': 'external_referral', 'justification': 'Attempting referral before ownership records are reviewed.'}).json()
    assert ref['status'] == 'BLOCKED'
    # Seed a pending decision through the same submit path on a case state that meets the evidence conditions is not
    # possible without fabricating evidence; instead verify the approval endpoint refuses any pending decision.
    with engine.begin() as con:
        con.execute(m.decisions.insert().values(decision_id='PD-test-pending', case_id=cid, action='external_referral', policy_version='CLAIMSHIELD-SIU-V1', evaluation={}, evidence_refs=[], justification='Test pending approval record.', requested_by='Investigator One', status='PENDING_APPROVAL'))
    own = client.post(A + '/decisions/PD-test-pending/approve', json={'actor': 'Investigator One'})
    other = client.post(A + '/decisions/PD-test-pending/approve', json={'actor': 'Somebody Else'})
    reject = client.post(A + '/decisions/PD-test-pending/reject', json={'actor': 'Somebody Else'})
    assert own.status_code == other.status_code == reject.status_code == 403 and 'own recommendation' in own.json()['detail']
    with engine.connect() as con: assert con.execute(select(m.decisions.c.status).where(m.decisions.c.decision_id == 'PD-test-pending')).scalar() == 'PENDING_APPROVAL'
    assert client.post(A + '/decisions/PD-missing/approve', json={}).status_code == 404

def test_queue_shows_decision_readiness_without_changing_ranking(client):
    q = client.get(A + '/siu/queue?capacity=100').json()
    assert q['decision_policy'] == 'CLAIMSHIELD-SIU-V1' and all(i['decision_readiness'] for i in q['items'])
    scores = [i['priority_score'] for i in q['items']]; assert scores == sorted(scores, reverse=True)
    by_case = {i['case_id']: i['decision_readiness'] for i in q['items']}
    p252 = case_for(client, 'P0252')
    if p252 in by_case: assert by_case[p252] == 'APPROVAL_REQUIRED'  # the pending decision recorded above

def test_reviewed_evidence_changes_eligibility(client):
    """A NEEDS_EVIDENCE case becomes eligible after an investigator records a reviewed supporting outcome."""
    q = client.get(A + '/siu/queue?capacity=100').json()['items']
    target = next(i['case_id'] for i in q if i['decision_readiness'] == 'MORE_EVIDENCE_NEEDED')
    rec = next((r for r in client.get(A + f'/cases/{target}/next-evidence').json()['recommendations'] if r['evidence_type'] != 'member_confirmation'), None)
    if rec is None: pytest.skip('No non-confirmation evidence type for this case')
    assert actions(client, target)['full_investigation']['status'] == 'NEEDS_EVIDENCE'
    req = client.post(A + f'/cases/{target}/evidence-requests', json={'finding_id': rec['finding_id'], 'evidence_type': rec['evidence_type']}).json()
    assert actions(client, target)['full_investigation']['status'] == 'NEEDS_EVIDENCE'  # requested is not reviewed
    client.post(A + f'/evidence-requests/{req["request_id"]}/outcome', json={'status': 'VERIFIED_SUPPORTS', 'notes': 'Reviewed document supports the concern.'})
    after = actions(client, target)['full_investigation']
    assert after['status'] == 'ALLOWED' and req['request_id'] in after['evidence_refs']
    assert next(i for i in client.get(A + '/siu/queue?capacity=100').json()['items'] if i['case_id'] == target)['decision_readiness'] == 'INVESTIGATION_REVIEW_ELIGIBLE'
