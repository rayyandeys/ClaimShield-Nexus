"""End-to-end live claims monitoring on the disposable claimshield_features_test database (after the feature suite).
Sessions are driven step by step (generate -> immediate screening -> enrichment -> lifecycle) with the same functions the
stream runner and worker call, so every assertion is about persisted state."""
import json
from datetime import timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update, delete
from app.core import engine, settings, now
from app import models as m
from app.main import app
from app.services import stream as st
from app.services.cases import case_inputs, rank_values

pytestmark = pytest.mark.features
A = '/api/v1'
R = 'RUNNER-test'
state = {}

@pytest.fixture(scope='module')
def client():
    if settings.db_name != 'claimshield_features_test': pytest.skip('Set DB_NAME=claimshield_features_test and migrate the isolated database.')
    with engine.connect() as con: ready = con.execute(select(func.count()).select_from(m.models).where(m.models.c.status == 'READY')).scalar()
    if not ready:
        from app.scenarios import build_snapshot
        from app.services.ingestion import import_dataset
        from app.services.pipeline import analyze
        import_dataset(); import_dataset(build_snapshot(settings.data_dir / 'synthetic', settings.artifact_dir / 'scenarios/v1')['snapshot']); analyze()
    with engine.begin() as con: con.execute(delete(m.stream_runners))
    with TestClient(app) as c: yield c

def beat():
    from sqlalchemy.dialects.postgresql import insert
    with engine.begin() as con:
        stmt = insert(m.stream_runners).values(runner_id='RUNNER-api-test', heartbeat_at=now(), host='test')
        con.execute(stmt.on_conflict_do_update(index_elements=['runner_id'], set_={'heartbeat_at': now()}))

def drive(sid, steps=None, enrich_every=20):
    """Generates up to `steps` arrivals, screening each and enriching every `enrich_every` claims."""
    done = 0
    while steps is None or done < steps:
        s = st.acquire(R)
        if not s or s['status'] != 'RUNNING' or s['generated_count'] >= s['intended_count']: break
        st.generate_one(s, R); done += 1
        while st.process_next(R): pass
        if done % enrich_every == 0: st.run_enrichment(sid)
    return done

def drain(sid):
    for _ in range(10):
        while st.process_next(R): pass
        st.run_enrichment(sid); result = st.advance(sid)
        if result in ('COMPLETED', 'STOPPED', 'FAILED'): return result
    return st.advance(sid)

def snapshot():
    with engine.connect() as con:
        return {'findings': {r[0]: tuple(r[1:]) for r in con.execute(select(m.findings.c.finding_id, m.findings.c.status, m.findings.c.severity, m.findings.c.data_completeness))},
                'audit': {r[0]: r[1] for r in con.execute(select(m.audit.c.event_id, m.audit.c.explanation))}, 'cases': {r[0]: r[1] for r in con.execute(select(m.cases.c.case_id, m.cases.c.case_status))},
                'requests': {r[0]: r[1] for r in con.execute(select(m.evidence_requests.c.request_id, m.evidence_requests.c.status))}}

def test_start_requires_runner_and_rejects_duplicates(client):
    state['before'] = snapshot()
    assert client.post(A + '/stream/sessions', json={'max_claims': 2}).status_code == 503
    beat()
    assert client.post(A + '/stream/sessions', json={'max_claims': 0}).status_code == 422
    r = client.post(A + '/stream/sessions', json={'max_claims': 2, 'scenario_mode': 'normal_only'}); assert r.status_code == 201
    sid = r.json()['session_id']; state['s1'] = sid; assert r.json()['status'] == 'STARTING'
    # Double click / concurrent start: the second request is rejected and no second session exists.
    assert client.post(A + '/stream/sessions', json={'max_claims': 2}).status_code == 409
    with engine.connect() as con: assert con.execute(select(func.count()).select_from(m.stream_sessions).where(m.stream_sessions.c.status.in_(m.STREAM_ACTIVE))).scalar() == 1

def test_insufficient_history_is_a_documented_terminal_outcome(client):
    sid = state['s1']; s = st.acquire(R); st.activate(s, R)
    assert drive(sid) == 2 and drain(sid) == 'COMPLETED'
    for claim in client.get(A + f'/stream/sessions/{sid}/claims').json()['items']:
        cov = client.get(A + f'/stream/claims/{claim["claim_id"]}/coverage').json(); stages = {x['stage']: x for x in cov['stages']}
        assert cov['fully_analyzed'] and stages['isolation_forest']['status'] == 'INSUFFICIENT_DATA' and stages['forecast']['status'] == 'INSUFFICIENT_DATA'
        assert 'no anomaly score is invented' in stages['isolation_forest']['reason'] and stages['phoenix']['status'] == 'COMPLETED'
        assert stages['supplytrace']['status'] == ('COMPLETED' if claim['claim_type'] == 'facility' else 'NOT_APPLICABLE')
        assert stages['case_consolidation']['status'] == 'NOT_APPLICABLE' and not cov['finding_ids']
    assert client.get(A + f'/stream/sessions/{sid}').json()['metrics']['fully_analyzed'] == 2

def test_full_session_generates_genuine_findings_cases_and_rankings(client):
    beat(); r = client.post(A + '/stream/sessions', json={'max_claims': 60}); assert r.status_code == 201
    sid = state['s2'] = r.json()['session_id']; s = st.acquire(R); st.activate(s, R)
    with engine.connect() as con: state['claims_before'] = con.execute(select(func.count()).select_from(m.tables['claims'])).scalar()
    assert drive(sid) == 60 and drain(sid) == 'COMPLETED'
    summary = client.get(A + f'/stream/sessions/{sid}/summary').json(); metrics = summary['metrics']; state['summary'] = summary
    assert metrics['generated'] == metrics['accepted'] == metrics['analyzed'] == metrics['fully_analyzed'] == 60 and metrics['failed'] == 0 and metrics['pending'] == 0
    types = {f['finding_type'] for f in summary['findings']}
    assert {'duplicate_billing', 'consumable_quantity_anomaly', 'impossible_timing', 'stolen_id_batch'} <= types
    assert summary['cases'] and all(c['priority_score'] > 0 and c['queue_position'] for c in summary['cases'])
    with engine.connect() as con: assert con.execute(select(func.count()).select_from(m.tables['claims'])).scalar() == state['claims_before'] + 60

def test_findings_are_source_linked_and_detail_pages_resolve(client):
    sid = state['s2']; items = {c['scenario']: c for c in client.get(A + f'/stream/sessions/{sid}/claims?page_size=200').json()['items']}
    dup = client.get(A + f'/claims/{items["duplicate_submission"]["claim_id"]}').json()
    finding = next(f for f in dup['findings'] if f['finding_type'] == 'duplicate_billing')
    assert len(finding['related_claim_ids']) == 2 and dup['cases'] and dup['stream']['session_id'] == sid
    trace = client.get(A + f'/claims/{items["supply_quantity_outlier"]["claim_id"]}/supplytrace').json()
    bandage = next(s for s in trace['supply_items'] if s['supply_code'] == 'BANDAGE'); cmp = trace['comparisons'][bandage['supply_id']]
    assert cmp['quantity']['available'] and cmp['quantity']['observed'] > cmp['quantity']['threshold'] and cmp['peer_count'] >= 20
    case = client.get(A + f'/cases/{dup["cases"][0]}').json()
    assert case['evidence'] and all(e['source_record_id'] for e in case['evidence']) and any(t['action_type'] == 'case_created' for t in case['timeline'])
    graph = client.get(A + '/network/' + case['primary_entity']).json()
    assert any(n['data']['id'] == items['duplicate_submission']['claim_id'] for n in graph['nodes']) or graph['total_neighbors'] > 0

def test_fully_analyzed_only_when_every_stage_is_terminal(client):
    with engine.connect() as con:
        bad = con.execute(select(func.count()).select_from(m.stream_claims.join(m.stream_stages)).where(m.stream_claims.c.analysis_status == 'FULLY_ANALYZED', ~m.stream_stages.c.status.in_(st.TERMINAL_OK))).scalar()
        stages = con.execute(select(func.count()).select_from(m.stream_stages).join(m.stream_claims).where(m.stream_claims.c.session_id == state['s2'])).scalar()
    assert bad == 0 and stages == 60 * len(st.STAGE_NAMES)

def test_radar_batch_and_member_rows_for_the_stream_cohort(client):
    provider = state['summary']['cohort']['providers']['C']; batch = client.get(A + f'/radar/batches/{provider}').json()
    assert batch['qualified'] and batch['metrics']['new_members'] >= 25 and batch['case_id']
    assert client.get(A + f'/radar/batches/{provider}/members').json()['total'] == batch['metrics']['new_members']
    from app.services.pipeline import scenario_evaluation
    with engine.connect() as con:
        batch_rows = [dict(r) for r in con.execute(select(m.radar_batches)).mappings()]; link_rows = [dict(r) for r in con.execute(select(m.successors)).mappings()]
    s = scenario_evaluation(batch_rows, link_rows)
    assert provider in s['live_stream_flags']['radar'] and provider not in s['unexpected_radar_flags'] and all(r['correct'] for r in s['radar'] + s['phoenix'])

def test_siu_priorities_use_the_existing_scorer_and_unique_exposure(client):
    with engine.connect() as con:
        for c in state['summary']['cases']:
            case, fs, cs, forecast = case_inputs(con, c['case_id'])
            expected = rank_values(fs, cs, forecast)
            assert case['priority_score'] == expected['priority_score'] and float(case['potential_financial_exposure']) == expected['potential_financial_exposure']
            active = {cid for f in fs if f['status'] in {'ACTIVE', 'UNRESOLVED', 'ESCALATED'} for cid in f['claim_ids']}
            assert expected['potential_financial_exposure'] == round(sum(float(x['allowed_amount_usd']) for x in cs if x['claim_id'] in active and x['claim_status'] == 'pending') + sum(float(x['paid_amount_usd']) for x in cs if x['claim_id'] in active and x['claim_status'] == 'paid'), 2)

def test_next_best_evidence_reads_the_updated_case(client):
    cid = state['summary']['cases'][0]['case_id']; nbe = client.get(A + f'/cases/{cid}/next-evidence').json()
    assert nbe['case_id'] == cid and nbe['current']['stored_priority'] == state['summary']['cases'][0]['priority_score'] and nbe['recommendations']
    rec = nbe['recommendations'][0]; before = snapshot()
    sim = client.post(A + f'/cases/{cid}/simulate', json={'finding_id': rec['finding_id'], 'outcome': 'explains'}).json()
    assert sim['hypothetical'] and snapshot() == before

def test_existing_reviews_and_audit_history_are_preserved(client):
    before, after = state['before'], snapshot()
    for fid, value in before['findings'].items(): assert after['findings'][fid] == value
    for eid, text in before['audit'].items(): assert after['audit'][eid] == text
    for cid, status in before['cases'].items(): assert after['cases'][cid] == status
    assert {k: after['requests'][k] for k in before['requests']} == before['requests']

def test_retries_and_duplicate_delivery_do_not_duplicate_findings(client):
    sid = state['s2']
    with engine.connect() as con:
        counts = lambda: tuple(con.execute(select(func.count()).select_from(t)).scalar() for t in [m.findings, m.finding_claims, m.evidence, m.case_findings, m.cases])
        before = counts()
    # Duplicate job delivery: a second enrichment run finds nothing to cover.
    assert st.run_enrichment(sid)['claims'] == 0
    # Re-delivery of an already screened claim (as after a crash before acknowledgement) re-runs screening idempotently.
    target = next(c for c in state['summary']['findings'] if c['finding_type'] == 'duplicate_billing')
    with engine.begin() as con:
        claim_id = con.execute(select(m.finding_claims.c.claim_id).where(m.finding_claims.c.finding_id == target['finding_id']).order_by(m.finding_claims.c.claim_id.desc())).scalars().first()
        con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).values(analysis_status='ACCEPTED', attempts=0))
    assert st.process_next(R) == claim_id
    st.run_enrichment(sid)
    with engine.connect() as con:
        assert tuple(con.execute(select(func.count()).select_from(t)).scalar() for t in [m.findings, m.finding_claims, m.evidence, m.case_findings, m.cases]) == before
        assert con.execute(select(m.stream_claims.c.analysis_status).where(m.stream_claims.c.claim_id == claim_id)).scalar() == 'FULLY_ANALYZED'
    assert st.advance(sid) == 'COMPLETED'

def test_events_are_ordered_committed_and_resumable(client):
    sid = state['s2']; first = client.get(A + f'/stream/events?session_id={sid}&limit=50').json()
    ids = [e['event_id'] for e in first['items']]; assert ids == sorted(ids) and len(set(ids)) == len(ids)
    rest, cursor = [], first['cursor']
    while True:
        page = client.get(A + f'/stream/events?session_id={sid}&after={cursor}&limit=200').json()
        assert all(e['event_id'] > cursor for e in page['items']); rest += page['items']; cursor = page['cursor']
        if not page['more']: break
    assert not set(ids) & {e['event_id'] for e in rest} and [e['event_id'] for e in rest] == sorted(e['event_id'] for e in rest)
    every = first['items'] + rest
    with engine.connect() as con: accepted = set(con.execute(select(m.stream_claims.c.claim_id).where(m.stream_claims.c.session_id == sid)).scalars())
    assert {e['claim_id'] for e in every if e['event_type'] == 'claim_persisted'} == accepted
    assert {e['claim_id'] for e in every if e['event_type'] == 'claim_fully_analyzed'} == accepted
    for e in every:
        if e['event_type'] == 'finding_created':
            with engine.connect() as con: assert con.execute(select(func.count()).select_from(m.findings).where(m.findings.c.finding_id == e['finding_id'])).scalar() == 1
    # SSE resumes after Last-Event-ID without repeating it (bounded connection for the test).
    body = client.get(A + f'/stream/events/subscribe?session_id={sid}&timeout=1', headers={'Last-Event-ID': str(ids[-1])}).text
    sent = [int(line[4:]) for line in body.splitlines() if line.startswith('id: ')]
    assert sent and sent == sorted(sent) and min(sent) > ids[-1] and set(sent) <= {e['event_id'] for e in rest}

def test_validation_rejection_failure_retry_and_stop_during_processing(client, monkeypatch):
    beat(); r = client.post(A + '/stream/sessions', json={'max_claims': 30}); sid = state['s3'] = r.json()['session_id']
    s = st.acquire(R); st.activate(s, R)
    # A malformed arrival is rejected by validation and nothing is persisted; the stream continues.
    s = st.acquire(R); bundle = st.gen.bundle(st.plan_for(s)[0], st.plan_for(s), s['session_number'], s['seed'])
    bad = dict(bundle, claims=[dict(bundle['claims'][0], allowed_amount_usd=bundle['claims'][0]['billed_amount_usd'] + Decimal('100'))])
    out = st.generate_one(s, R, bad); assert not out['accepted'] and any(e['message'] == 'Allowed exceeds billed' for e in out['errors'])
    with engine.connect() as con: assert not con.execute(select(func.count()).select_from(m.tables['claims']).where(m.tables['claims'].c.claim_id == out['claim_id'])).scalar()
    # A competing runner cannot generate while the lease is held; a stale session snapshot cannot create a duplicate.
    assert st.acquire('RUNNER-other') is None
    with pytest.raises(st.Conflict): st.generate_one(dict(s, generated_count=0), R)
    # Immediate screening failure: retried, then FAILED after the attempt limit, never fully analyzed.
    s = st.acquire(R); generated = st.generate_one(s, R); claim_id = generated['claim_id']
    def broken(_): raise RuntimeError('simulated engine outage')
    for attempt in range(st.MAX_ATTEMPTS):
        with engine.begin() as con: con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).values(next_attempt_at=None))
        assert st.process_next(R, broken) == claim_id
    with engine.begin() as con: con.execute(update(m.stream_claims).where(m.stream_claims.c.claim_id == claim_id).values(next_attempt_at=None))
    st.process_next(R)
    cov = client.get(A + f'/stream/claims/{claim_id}/coverage').json()
    assert cov['analysis_status'] == 'FAILED' and not cov['fully_analyzed'] and {x['stage']: x['status'] for x in cov['stages']}['claim_rules'] == 'FAILED'
    # Controlled retry succeeds.
    assert client.post(A + f'/stream/claims/{claim_id}/retry').json()['analysis_status'] == 'RETRYING'
    assert st.process_next(R) == claim_id
    # Model unavailable during enrichment: ML stage FAILED, claims not fully analyzed, other engines still complete.
    import app.services.ml as ml
    original = ml.latest_ready_model
    def missing(kind):
        if kind == 'isolation_forest': raise FileNotFoundError('No READY isolation_forest model artifact is available')
        return original(kind)
    monkeypatch.setattr(ml, 'latest_ready_model', missing)
    st.run_enrichment(sid)
    cov = client.get(A + f'/stream/claims/{claim_id}/coverage').json(); stages = {x['stage']: x['status'] for x in cov['stages']}
    assert stages['isolation_forest'] == 'FAILED' and stages['member_radar'] == 'COMPLETED' and cov['analysis_status'] == 'ENRICH_RETRY' and not cov['fully_analyzed']
    monkeypatch.setattr(ml, 'latest_ready_model', original)
    # Stop during processing: no further generation, accepted claims drain to a terminal state.
    drive(sid, steps=5, enrich_every=100)
    stop = client.post(A + f'/stream/sessions/{sid}/stop').json(); assert stop['acknowledged'] and stop['status'] == 'STOPPING'
    s = st.acquire(R)
    with pytest.raises(st.Conflict): st.generate_one(s, R)
    assert st.advance(sid) == 'DRAINING'
    # Worker interruption: a run left RUNNING is superseded and its claims are covered by the next run.
    with engine.begin() as con:
        con.execute(m.stream_runs.insert().values(run_id=f'RUN-crashed-{sid}', session_id=sid, status='RUNNING', claim_ids=[], providers=[], stage_results={}))
    assert drain(sid) == 'STOPPED'
    final = client.get(A + f'/stream/sessions/{sid}').json()['metrics']
    # Arrival #5 re-submits #1, which was rejected and never persisted: its encounter reference is broken, so validation
    # rejects it too. 7 generated = 2 rejected + 5 accepted, all accepted claims fully analyzed.
    assert final['generated'] == 1 + 1 + 5 and final['rejected'] == 2 and final['accepted'] == 5 and final['fully_analyzed'] == 5 and final['pending'] == 0
    with engine.connect() as con:
        reasons = [e['payload']['errors'] for e in st.events_after(con, sid, 0, 500) if e['event_type'] == 'claim_rejected']
    assert any(x['message'] == 'Allowed exceeds billed' for x in reasons[0]) and any(x['message'] == 'Broken reference to encounters' for x in reasons[1])
    with engine.connect() as con: assert con.execute(select(m.stream_runs.c.status).where(m.stream_runs.c.run_id == f'RUN-crashed-{sid}')).scalar() == 'INTERRUPTED'
    assert client.post(A + f'/stream/sessions/{sid}/stop').json()['acknowledged'] is False

def test_replay_with_same_seed_uses_new_identifiers(client):
    beat(); r = client.post(A + '/stream/sessions', json={'max_claims': 12}); sid = r.json()['session_id']
    s = st.acquire(R); st.activate(s, R); drive(sid); assert drain(sid) == 'COMPLETED'
    with engine.connect() as con:
        a = [r[0] for r in con.execute(select(m.stream_claims.c.scenario).where(m.stream_claims.c.session_id == state['s2']).order_by(m.stream_claims.c.sequence).limit(12))]
        b = [r[0] for r in con.execute(select(m.stream_claims.c.scenario).where(m.stream_claims.c.session_id == sid).order_by(m.stream_claims.c.sequence))]
        dupes = con.execute(select(func.count()).select_from(m.findings).join(m.finding_claims).join(m.stream_claims, m.stream_claims.c.claim_id == m.finding_claims.c.claim_id).where(m.stream_claims.c.session_id == sid, m.findings.c.finding_type == 'duplicate_billing')).scalar()
    assert a == b and dupes == 2  # the replay's own duplicate pair only; nothing matches the earlier session's claims

def test_cohort_pools_give_each_role_its_members_without_overlap(client):
    need = st.gen.roles_needed(60, 'mixed_demo')
    with engine.connect() as con:
        for seed in range(5):
            pools = st.context_pools(con, st.gen.ROLES, seed, need)
            assert len(pools['A']) == need['A'] and len(pools['B']) == need['B']
            assert not {m_ for m_, _ in pools['A']} & {m_ for m_, _ in pools['B']}
            used = set(con.execute(select(m.tables['claims'].c.member_id).where(m.tables['claims'].c.provider_id.like('PS%'))).scalars())
            flagged = set(con.execute(select(m.radar_batch_members.c.member_id).join(m.radar_batches, m.radar_batches.c.provider_id == m.radar_batch_members.c.provider_id).where(m.radar_batches.c.qualified.is_(True), ~m.radar_batches.c.provider_id.like('PS%'))).scalars())
            chosen = {m_ for p in pools.values() for m_, _ in p}
            assert not chosen & used and not chosen & flagged

def test_unrecoverable_session_error_fails_visibly_and_frees_the_slot(client):
    beat(); sid = client.post(A + '/stream/sessions', json={'max_claims': 5}).json()['session_id']
    st.fail_session(sid, 'Cohort preparation failed: simulated')
    s = client.get(A + f'/stream/sessions/{sid}').json(); assert s['status'] == 'FAILED' and 'simulated' in s['error']['reason']
    assert any(e['event_type'] == 'session_failed' for e in client.get(A + f'/stream/events?session_id={sid}').json()['items'])
    beat(); r = client.post(A + '/stream/sessions', json={'max_claims': 1}); assert r.status_code == 201
    client.post(A + f'/stream/sessions/{r.json()["session_id"]}/stop'); assert st.advance(r.json()['session_id']) == 'STOPPED'
