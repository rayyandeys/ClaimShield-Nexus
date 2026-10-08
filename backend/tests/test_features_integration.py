"""End-to-end Next-Best-Evidence, Member Radar, confirmation loop and Phoenix workflow.
Runs only against DB_NAME=claimshield_features_test, which scripts/test.ps1 recreates for every run."""
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from app.core import engine, settings
from app import models as m
from app.main import app
from app.scenarios import build_snapshot
from app.services.ingestion import import_dataset
from app.services.pipeline import analyze

pytestmark = pytest.mark.features
A = '/api/v1'

@pytest.fixture(scope='module')
def client():
    if settings.db_name != 'claimshield_features_test': pytest.skip('Set DB_NAME=claimshield_features_test and migrate the isolated database.')
    import_dataset()
    built = build_snapshot(settings.data_dir / 'synthetic', settings.artifact_dir / 'scenarios/v1')
    report = import_dataset(built['snapshot']); assert report['inserted_counts']['claims'] == 1151 and report['inserted_counts']['members'] == 0
    result = analyze(); assert result['member_radar']['qualified_batches'] == 1 and result['phoenix']['flagged'] == 3
    with TestClient(app) as c: yield c

def case_of(client, provider):
    return next(l['case_id'] for l in client.get(A + f'/providers/{provider}/predecessors').json()['items'] if l['flagged'])

def finding_of(case, ftype):
    return next(f for f in case['findings'] if f['finding_type'] == ftype)

def fingerprint():
    """Row counts plus content hashes of every table a simulation could conceivably touch."""
    out = {}
    with engine.connect() as con:
        for t in [m.findings, m.cases, m.audit, m.actions, m.evidence, m.evidence_requests, m.confirmations, m.case_findings, m.member_risk, m.successors]:
            rows = con.execute(select(t)).mappings().all()
            out[t.name] = (len(rows), hashlib.sha256(json.dumps(sorted(json.dumps(dict(r), default=str, sort_keys=True) for r in rows)).encode()).hexdigest())
    return out

def test_scenarios_and_unexpected_flags(client):
    s = client.get(A + '/evaluation/summary').json()['scenarios']
    assert all(r['correct'] for r in s['radar'] + s['phoenix']) and not s['unexpected_radar_flags'] and not s['unexpected_phoenix_flags']

def test_original_results_unchanged_by_augmentation(client):
    d = client.get(A + '/evaluation/summary').json()['detection']['baselines']
    assert {k: v['precision_at_k'] for k, v in d.items()} == {'rules_only': .39, 'rules_plus_ml': .59, 'rules_ml_graph_supplytrace': .92}

def test_consolidated_provider_case_without_predecessor_findings(client):
    cid = case_of(client, 'P0252'); case = client.get(A + f'/cases/{cid}').json()
    types = {f['finding_type'] for f in case['findings']}
    assert {'stolen_id_batch', 'phoenix_successor', 'member_id_possibly_compromised'} <= types
    assert all(f['entity_id'] != 'P0251' for f in case['findings'])
    with engine.connect() as con:
        predecessor_claims = set(con.execute(select(m.tables['claims'].c.claim_id).where(m.tables['claims'].c.provider_id == 'P0251')).scalars())
        case_claims = {c['claim_id'] for c in case['claims']}
        assert len(case_claims & predecessor_claims) == 0
        # Exposure counts each claim once even though batch, member and successor findings share claims.
        unique = sum(float(c['paid_amount_usd']) if c['claim_status'] == 'paid' else float(c['allowed_amount_usd']) if c['claim_status'] == 'pending' else 0 for c in case['claims'])
    assert case['potential_financial_exposure'] == pytest.approx(unique, abs=.01)
    assert cid in [x['case_id'] for x in client.get(A + '/siu/queue?capacity=100').json()['items']]

def test_radar_endpoints(client):
    batches = client.get(A + '/radar/batches').json()['items']
    flags = {b['provider_id']: b['qualified'] for b in batches}
    assert flags['P0252'] is True and flags['P0259'] is False
    detail = client.get(A + '/radar/batches/P0252').json()
    assert detail['metrics']['new_members'] == 270 and detail['case_id'] and any(c['name'] == 'geographic_dispersion_percentile' and not c['required'] for c in detail['checks'])
    page1 = client.get(A + '/radar/batches/P0252/members?page=1&page_size=25').json(); page2 = client.get(A + '/radar/batches/P0252/members?page=2&page_size=25').json()
    assert page1['total'] == 270 and not {x['member_id'] for x in page1['items']} & {x['member_id'] for x in page2['items']}
    graph = client.get(A + '/radar/batches/P0252/graph?limit=50').json()
    assert graph['truncated'] and all(e['data']['source'] == 'P0252' for e in graph['edges'])
    member = page1['items'][0]['member_id']
    risk = client.get(A + f'/members/{member}/risk').json()
    assert risk['status'] == 'READY' and set(risk['signals']['signals']) == {'first_contact_burst', 'care_history_mismatch', 'geographic_jump_km', 'missing_encounter_rate', 'cross_provider_duplicates'}

def test_phoenix_endpoints_and_graph_edge(client):
    links = client.get(A + '/phoenix/links').json()['items']
    flagged = {(l['predecessor_id'], l['successor_id']) for l in links if l['flagged']}
    assert flagged == {('P0251', 'P0252'), ('P0253', 'P0254'), ('P0257', 'P0258')}
    subtle = next(l for l in links if l['successor_id'] == 'P0254' and l['predecessor_id'] == 'P0253')
    assert subtle['components']['shared_identifiers'] == 0 and subtle['flagged']
    lookalike = next(l for l in links if (l['predecessor_id'], l['successor_id']) == ('P0255', 'P0256'))
    assert not lookalike['flagged'] and list(lookalike['details']['matched_identifiers']) == ['address']
    detail = client.get(A + f'/phoenix/links/{subtle["link_id"]}').json(); assert detail['case_id']
    assert client.get(A + '/providers/P0251/successors').json()['items'][0]['successor_id'] == 'P0252'
    edges = [e['data'] for e in client.get(A + '/network/P0252').json()['edges'] if e['data']['type'] == 'possible_successor']
    assert len(edges) == 1 and edges[0]['source'] == 'P0251' and edges[0]['target'] == 'P0252' and edges[0]['verification_status'] == 'algorithmic_similarity'

def test_next_evidence_uses_real_scorer(client):
    cid = case_of(client, 'P0252'); rec = client.get(A + f'/cases/{cid}/next-evidence?capacity=10').json()
    stored = client.get(A + f'/cases/{cid}').json()['priority_score']
    assert rec['current']['priority'] == pytest.approx(stored) and rec['hypothetical'] and 'rank_values' in rec['scorer']
    assert 1 <= len(rec['recommendations']) <= 3
    values = [r['decision_value'] for r in rec['recommendations']]; assert values == sorted(values, reverse=True)
    assert all(r['probability_source'] in {'synthetic_default_assumption', 'insufficient_history', 'synthetic_historical_estimate'} for r in rec['recommendations'])
    assert client.get(A + '/cases/NOPE/next-evidence').status_code == 404
    summary = client.get(A + '/siu/queue/next-evidence?capacity=5').json()
    assert summary['items'] and all('decision_value' in x for x in summary['items'])

def test_simulation_performs_no_writes(client):
    cid = case_of(client, 'P0252'); case = client.get(A + f'/cases/{cid}').json(); f = finding_of(case, 'phoenix_successor')
    before = fingerprint()
    sims = [client.post(A + f'/cases/{cid}/simulate', json={'finding_id': f['finding_id'], 'outcome': o, 'capacity': 10}).json() for o in ['explains', 'supports']]
    assert fingerprint() == before
    assert all(s['hypothetical'] for s in sims) and sims[0]['simulated']['priority'] < sims[0]['original']['priority']
    assert sims[0]['original']['queue_position'] == client.get(A + f'/cases/{cid}/next-evidence').json()['current']['queue_position']
    assert client.post(A + f'/cases/{cid}/simulate', json={'finding_id': 'F-INVALID', 'outcome': 'explains'}).status_code == 422
    # The database itself rejects writes inside the simulation transaction.
    from app.main import read_only
    with read_only() as con, pytest.raises(Exception, match='read-only'):
        con.execute(text("UPDATE cases SET priority_score = 0 WHERE case_id = :c"), {'c': cid})

def test_evidence_request_lifecycle_and_acquisition_resolution(client):
    cid = case_of(client, 'P0258'); case = client.get(A + f'/cases/{cid}').json(); f = finding_of(case, 'phoenix_successor')
    rec = client.get(A + f'/cases/{cid}/next-evidence').json()
    assert any(r['evidence_type'] == 'ownership_acquisition_records' for r in rec['recommendations'])
    assert client.post(A + f'/cases/{cid}/evidence-requests', json={'finding_id': f['finding_id'], 'evidence_type': 'timestamp_audit'}).status_code == 422
    assert client.post(A + f'/cases/{cid}/evidence-requests', json={'finding_id': 'F-NOPE', 'evidence_type': 'ownership_acquisition_records'}).status_code == 422
    first = client.post(A + f'/cases/{cid}/evidence-requests', json={'finding_id': f['finding_id'], 'evidence_type': 'ownership_acquisition_records'})
    assert first.status_code == 201 and first.json()['status'] == 'REQUESTED' and first.json()['probability_source'] == 'synthetic_default_assumption'
    again = client.post(A + f'/cases/{cid}/evidence-requests', json={'finding_id': f['finding_id'], 'evidence_type': 'ownership_acquisition_records'}).json()
    assert again['duplicate'] and again['request_id'] == first.json()['request_id']
    assert not client.get(A + f'/cases/{cid}/next-evidence').json()['recommendations'] or all(r['evidence_type'] != 'ownership_acquisition_records' or r['finding_id'] != f['finding_id'] for r in client.get(A + f'/cases/{cid}/next-evidence').json()['recommendations'])
    rid = first.json()['request_id']; evidence_before = len(f['evidence_ids'])
    assert client.post(A + f'/evidence-requests/{rid}/outcome', json={'status': 'VERIFIED_EXPLAINS', 'notes': 'short'}).status_code == 422
    assert client.post(A + f'/evidence-requests/{rid}/outcome', json={'status': 'RECEIVED', 'notes': 'Filing received from the state registry.'}).status_code == 200
    done = client.post(A + f'/evidence-requests/{rid}/outcome', json={'status': 'VERIFIED_EXPLAINS', 'notes': 'Acquisition filing REL-documented; patient transfer is a legitimate practice purchase.'})
    assert done.status_code == 200 and done.json()['evidence_id']
    after = client.get(A + f'/cases/{cid}').json(); g = finding_of(after, 'phoenix_successor')
    assert g['status'] == 'EXPLAINED' and len(g['evidence_ids']) == evidence_before + 1 and after['evidence_version'] > case['evidence_version']
    assert after['priority_score'] < case['priority_score']
    kinds = {e['action_type'] for e in after['timeline']}
    assert {'evidence_request_created', 'evidence_received_awaiting_review', 'evidence_outcome_reviewed', 'finding_resolution'} <= kinds
    assert client.post(A + f'/evidence-requests/{rid}/outcome', json={'status': 'WITHDRAWN'}).status_code == 409
    assert client.post(A + f'/evidence-requests/{rid}/outcome', json={'status': 'VERIFIED_EXPLAINS', 'notes': 'repeat submission is idempotent'}).json()['idempotent']

def test_member_confirmation_loop(client):
    cid = case_of(client, 'P0252'); case = client.get(A + f'/cases/{cid}').json(); batch = finding_of(case, 'stolen_id_batch')
    claims = {c['claim_id']: c for c in case['claims']}; batch_claims = [c for c in sorted(batch['related_claim_ids'])]
    other_claim = client.get(A + '/claims?provider_id=P0001&page_size=1').json()['items'][0]
    assert client.post(A + f'/cases/{cid}/confirmations', json={'claim_id': other_claim['claim_id']}).status_code == 422
    assert client.post(A + f'/cases/{cid}/confirmations', json={'claim_id': batch_claims[0], 'member_id': 'M999999'}).status_code == 422
    notices = []
    for claim_id in batch_claims[:5]:
        r = client.post(A + f'/cases/{cid}/confirmations', json={'claim_id': claim_id}); assert r.status_code == 201; notices.append(r.json())
    n = notices[0]
    assert n['simulated'] and n['response'] == 'PENDING' and n['member_id'] == claims[n['claim_id']]['member_id'] and n['notice_text'].startswith('SIMULATED NOTICE')
    assert client.post(A + f'/cases/{cid}/confirmations', json={'claim_id': n['claim_id']}).json()['duplicate']
    # Pending responses cannot be reviewed and cause no change.
    assert client.post(A + f'/evidence-requests/{n["request_id"]}/outcome', json={'status': 'VERIFIED_SUPPORTS', 'notes': 'No response recorded yet.'}).status_code == 409
    def respond(note, answer): return client.post(A + f'/confirmations/{note["confirmation_id"]}/response', json={'response': answer})
    def review(note, status): return client.post(A + f'/evidence-requests/{note["request_id"]}/outcome', json={'status': status, 'notes': f'Reviewed simulated member response for {note["claim_id"]}.'})
    assert respond(n, 'NO').status_code == 200 and respond(n, 'NO').json()['idempotent'] and respond(n, 'YES').status_code == 409
    assert review(n, 'VERIFIED_EXPLAINS').status_code == 422
    before = client.get(A + f'/cases/{cid}').json(); assert finding_of(before, 'stolen_id_batch')['severity'] == 'MEDIUM'
    assert review(n, 'VERIFIED_SUPPORTS').status_code == 200
    one = client.get(A + f'/cases/{cid}').json(); b1 = finding_of(one, 'stolen_id_batch')
    assert b1['status'] == 'ESCALATED' and b1['data_completeness'] == 1.0 and b1['severity'] == 'MEDIUM' and b1['status'] != 'EXPLAINED'
    assert review(n, 'VERIFIED_SUPPORTS').json()['idempotent']  # a repeated review cannot inflate the denial count
    for note in notices[1:3]: respond(note, 'NO'); assert review(note, 'VERIFIED_SUPPORTS').status_code == 200
    three = client.get(A + f'/cases/{cid}').json(); assert finding_of(three, 'stolen_id_batch')['severity'] == 'HIGH'
    # Yes explains only that member's allegation; Not sure has no score effect.
    respond(notices[3], 'YES'); assert review(notices[3], 'VERIFIED_EXPLAINS').status_code == 200
    after_yes = client.get(A + f'/cases/{cid}').json()
    assert finding_of(after_yes, 'stolen_id_batch')['status'] == 'ESCALATED'
    respond(notices[4], 'NOT_SURE'); assert review(notices[4], 'VERIFIED_SUPPORTS').status_code == 422
    priority = client.get(A + f'/cases/{cid}').json()['priority_score']
    assert review(notices[4], 'INCONCLUSIVE').status_code == 200 and client.get(A + f'/cases/{cid}').json()['priority_score'] == priority
    listed = client.get(A + f'/cases/{cid}/confirmations').json(); assert listed['simulated'] and len(listed['items']) == 5
    kinds = [e['action_type'] for e in client.get(A + f'/cases/{cid}').json()['timeline']]
    assert kinds.count('simulated_member_notice_generated') == 5 and 'simulated_member_response_recorded' in kinds and 'finding_corroborated' in kinds and 'member_contacted' not in kinds

def test_spillover_is_leads_only(client):
    cid = case_of(client, 'P0252'); before = client.get(A + f'/cases/{cid}').json()
    s = client.get(A + f'/cases/{cid}/spillover').json()
    case_claims = {c['claim_id'] for c in before['claims']}
    assert s['applicable'] and s['totals']['claims'] > 0 and not {c['claim_id'] for c in s['claims']} & case_claims
    assert sum(g['claims'] for g in s['groups']) == s['totals']['claims'] and sum(g['paid'] for g in s['groups']) == pytest.approx(s['totals']['paid'], abs=.05)
    assert client.get(A + f'/cases/{cid}').json()['potential_financial_exposure'] == before['potential_financial_exposure']

def test_reanalysis_is_stable(client):
    """Step 14–15: rerun after reviewed outcomes; no duplicates and no reversal of any reviewed decision."""
    tables = [m.findings, m.successors, m.cases, m.radar_batches, m.radar_batch_members, m.evidence, m.evidence_requests, m.confirmations, m.case_findings]
    def snapshot(con):
        return {'counts': {t.name: con.execute(select(func.count()).select_from(t)).scalar() for t in tables},
                'findings': {r.finding_id: (r.status, r.severity, r.data_completeness) for r in con.execute(select(m.findings).where(m.findings.c.status != 'ACTIVE'))},
                'requests': {r.request_id: r.status for r in con.execute(select(m.evidence_requests))},
                'responses': {r.confirmation_id: r.response for r in con.execute(select(m.confirmations))},
                'flagged_links': sorted(r.link_id for r in con.execute(select(m.successors).where(m.successors.c.flagged.is_(True))))}
    with engine.connect() as con:
        before = snapshot(con); audit_before = con.execute(select(func.count()).select_from(m.audit)).scalar()
        assert before['findings'] and before['requests'] and before['responses']
    analyze()
    with engine.connect() as con:
        after = snapshot(con)
        assert after == before
        # Reanalysis appends no review events; only new cases would add case_created events and none are created.
        assert con.execute(select(func.count()).select_from(m.audit)).scalar() == audit_before
