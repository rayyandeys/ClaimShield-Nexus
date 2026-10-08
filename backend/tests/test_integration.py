"""Runs against a dedicated DB_NAME=claimshield_test; never mutates the demo DB."""
import json
from types import SimpleNamespace
from datetime import timedelta
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select,func,delete,update
from app.core import engine,settings,now
from app import models as m
from app.main import app
from app.services.ingestion import import_dataset
from app.services.pipeline import analyze
from app.services.repository import case_detail
from app.services.briefs import compose,fallback

pytestmark=pytest.mark.integration
CONTROLLED_RESULTS=[]

@pytest.fixture(scope='module')
def client():
    if settings.db_name!='claimshield_test':pytest.skip('Set DB_NAME=claimshield_test and migrate the isolated database.')
    report=import_dataset();assert report['counts']['claims']==20000
    repeated=import_dataset();assert sum(repeated['inserted_counts'].values())==0
    analysis=analyze();assert analysis['findings']>0 and analysis['cases']>0
    with TestClient(app) as client:yield client

def test_health_summary_pagination_no_labels(client):
    assert client.get('/health').json()['status']=='ok'
    summary=client.get('/api/v1/dataset/summary').json();assert summary['counts']['claims']==20000
    a=client.get('/api/v1/claims?page=1&page_size=25').json();b=client.get('/api/v1/claims?page=2&page_size=25').json()
    assert a['total']==20000 and len(a['items'])==25
    assert not {c['claim_id'] for c in a['items']}&{c['claim_id'] for c in b['items']}
    assert 'scenario_type' not in json.dumps(a) and 'injected_suspicious_pattern' not in json.dumps(a)
    assert client.get('/api/v1/claims?page_size=99999').status_code==422
    assert client.get('/api/v1/claims/NOT_FOUND').status_code==404

def test_models_train_and_temporal_windows(client):
    status=client.get('/api/v1/models/status').json()
    assert sum(m['status']=='READY' for m in status['items'])>=4
    from datetime import date
    import joblib
    for model in status['items']:
        if not model['model_type'].startswith('forecast_'):continue
        meta=model['metadata'];split=meta['splits'];h=meta['horizon']
        assert date.fromisoformat(split['train']['end'])+timedelta(days=h)<date.fromisoformat(split['validation']['start'])
        assert date.fromisoformat(split['validation']['end'])+timedelta(days=h)<date.fromisoformat(split['test']['start'])
        assert meta['test']['n']>0 and 0<=meta['test']['brier']<=1
        saved=joblib.load(model['artifact_path']);assert saved['features']==meta['features']
    assert len(client.get('/api/v1/providers/P0001/forecast').json()['items'])==3

def test_network_supplytrace_and_capacity(client):
    graph=client.get('/api/v1/network/P0001?limit=40').json()
    assert 1<=len(graph['nodes'])<=40 and len(graph['edges'])<=300
    ids={n['data']['id'] for n in graph['nodes']}
    assert all(e['data']['source'] in ids and e['data']['target'] in ids for e in graph['edges'])
    claim=client.get('/api/v1/claims?has_supplies=true&page_size=1').json()['items'][0]
    supply=client.get('/api/v1/claims/'+claim['claim_id']+'/supplytrace').json()
    assert supply['supply_items'] and supply['comparisons']
    queue=client.get('/api/v1/siu/queue?capacity=5').json()
    assert len(queue['items'])==5
    assert queue['items'][0]['priority_score']>=queue['items'][-1]['priority_score']

def test_full_human_review_path_and_audit(client):
    row=client.get('/api/v1/siu/queue?capacity=1').json()['items'][0];cid=row['case_id']
    before=client.get('/api/v1/cases/'+cid).json();finding=before['findings'][0]
    assert client.post('/api/v1/cases/'+cid+'/actions',json={'action_type':'status','value':'ESCALATED','explanation':'Test invalid transition'}).status_code==422
    for request in [{'action_type':'assign','value':'Synthetic QA Investigator','explanation':'Isolated integration test assignment.'},{'action_type':'status','value':'UNDER_REVIEW','explanation':'Review original synthetic evidence.'}]:assert client.post('/api/v1/cases/'+cid+'/actions',json=request).status_code==200
    brief=client.post('/api/v1/cases/'+cid+'/brief').json();assert brief['mode']=='deterministic'
    challenger=client.post('/api/v1/cases/'+cid+'/challenge').json();assert challenger['content']['synthesis']['alternatives']
    response=client.post('/api/v1/findings/'+finding['finding_id']+'/resolve',json={'status':'EXPLAINED','explanation':'Isolated test: reviewed the supplied source record and recorded an explanation.','verified_evidence_ids':[finding['evidence_ids'][0]]})
    assert response.status_code==200
    after=client.get('/api/v1/cases/'+cid).json()
    assert after['evidence_version']>before['evidence_version']
    assert after['priority_score']<=before['priority_score']
    assert any(e['action_type']=='finding_resolution' for e in after['timeline'])
    assert any(f['finding_id']==finding['finding_id'] and f['status']=='EXPLAINED' for f in after['findings'])
    invalid=client.post('/api/v1/findings/'+finding['finding_id']+'/resolve',json={'status':'EXPLAINED','explanation':'Attempting unsupported evidence reference','verified_evidence_ids':['INVENTED']})
    assert invalid.status_code==422
    with engine.begin() as con:
        with pytest.raises(Exception):con.execute(update(m.audit).where(m.audit.c.case_id==cid).values(explanation='tamper'))

def test_repeat_analysis_stable_and_preserves_resolution(client):
    with engine.connect() as con:
        before={t.name:con.execute(select(func.count()).select_from(t)).scalar() for t in [m.findings,m.cases,m.evidence]}
        resolved=set(con.execute(select(m.findings.c.finding_id).where(m.findings.c.status=='EXPLAINED')).scalars())
    analyze()
    with engine.connect() as con:
        after={t.name:con.execute(select(func.count()).select_from(t)).scalar() for t in [m.findings,m.cases,m.evidence]}
        assert before==after
        assert resolved<=set(con.execute(select(m.findings.c.finding_id).where(m.findings.c.status=='EXPLAINED')).scalars())

@pytest.mark.parametrize('mode',['valid','malformed','invented','rate_limit','timeout'])
def test_groq_controlled_integration(client,monkeypatch,mode):
    cid=client.get('/api/v1/siu/queue?capacity=1').json()['items'][0]['case_id']
    with engine.begin() as con:
        con.execute(delete(m.briefs).where(m.briefs.c.case_id==cid))
        case=case_detail(con,cid)
    synthesis=fallback(case)
    if mode=='invented':synthesis.facts[0].evidence_id='FAKE-EVIDENCE'
    content='{malformed' if mode=='malformed' else synthesis.model_dump_json()
    def create(**kwargs):
        if mode=='timeout':raise httpx.ReadTimeout('controlled timeout')
        if mode=='rate_limit':
            from groq import RateLimitError
            raise RateLimitError('controlled rate limit',response=httpx.Response(429,request=httpx.Request('POST','https://api.groq.com')),body={})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    mock=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result=compose(cid,client=mock)
    assert result['mode']==('groq' if mode=='valid' else 'deterministic')
    assert 'FAKE-EVIDENCE' not in json.dumps(result)
    CONTROLLED_RESULTS.append({'input':mode,'output_mode':result['mode'],'invented_evidence_present':'FAKE-EVIDENCE' in json.dumps(result)})

def test_job_claim_and_restart_lease(client):
    from app.worker import enqueue,claim_job
    from app.core import now
    job=enqueue('analysis');claimed=claim_job();assert claimed['job_id']==job['job_id']
    assert claim_job() is None
    with engine.begin() as con:con.execute(update(m.jobs).where(m.jobs.c.job_id==job['job_id']).values(lease_until=now()-timedelta(seconds=1)))
    recovered=claim_job();assert recovered['job_id']==job['job_id']
    with engine.begin() as con:con.execute(update(m.jobs).where(m.jobs.c.job_id==job['job_id']).values(status='COMPLETED',completed_at=now()))

def test_write_controlled_verification_report(client):
    report={'status':'CALCULATED','controlled_responses':len(CONTROLLED_RESULTS),'valid_structured_response_accepted':sum(r['input']=='valid' and r['output_mode']=='groq' for r in CONTROLLED_RESULTS),'invalid_or_unavailable_responses_fell_back':sum(r['input']!='valid' and r['output_mode']=='deterministic' for r in CONTROLLED_RESULTS),'invented_evidence_accepted':sum(r['invented_evidence_present'] for r in CONTROLLED_RESULTS),'observations':CONTROLLED_RESULTS,'limitation':'Controlled fixtures exercise structural and source-value verification; this is not a real-world factuality benchmark or a live Groq test.'}
    (settings.artifact_dir/'evaluation/brief_test_report.json').write_text(json.dumps(report,indent=2))
