import json
from datetime import date
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, or_, text
from app.core import engine, settings, clean
from app import models as m
from app.schemas import Page, ObjectResponse, JobRequest, ActionInput, ResolutionInput
from app.services.repository import case_detail, get_findings, load_data
from app.services.cases import apply_action,resolve,ACTIVE,TRANSITIONS
from app.worker import enqueue

app=FastAPI(title='ClaimShield Nexus',version='1.0.0',description='Synthetic healthcare investigation decision support. Demo analyst identity; no production authentication.')
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],allow_methods=['GET','POST'],allow_headers=['Content-Type'])
API='/api/v1'

@app.get('/health',response_model=ObjectResponse)
def health():
    with engine.connect() as con:con.execute(text('SELECT 1'))
    return {'status':'ok','database':'connected','synthetic_only':True}

def one(con,table,key,value):
    row=con.execute(select(table).where(table.c[key]==value)).mappings().first()
    if not row:raise HTTPException(404,'Record not found')
    return dict(row)

def paginated(con,table,conditions,page,size,order=None):
    query=select(table).where(*conditions)
    total=con.execute(select(func.count()).select_from(table).where(*conditions)).scalar()
    rows=con.execute(query.order_by(order if order is not None else list(table.primary_key)[0],list(table.primary_key)[0]).offset((page-1)*size).limit(size)).mappings()
    return clean({'items':[dict(r) for r in rows],'total':total,'page':page,'page_size':size})

@app.get(API+'/dataset/summary',response_model=ObjectResponse)
def summary():
    with engine.connect() as con:
        counts={name:con.execute(select(func.count()).select_from(t)).scalar() for name,t in m.tables.items()}
        claims=m.tables['claims'];date_range=con.execute(select(func.min(claims.c.service_date),func.max(claims.c.service_date))).one()
        active=con.execute(select(func.count()).select_from(m.findings).where(m.findings.c.status.in_(ACTIVE))).scalar()
        cases_count=con.execute(select(func.count()).select_from(m.cases).where(~m.cases.c.case_status.in_(['CLOSED','RESOLVED']))).scalar()
        # Global exposure uses unique claims, never sums potentially overlapping cases.
        flagged=select(m.finding_claims.c.claim_id).join(m.findings).where(m.findings.c.status.in_(ACTIVE)).distinct()
        cs=list(con.execute(select(claims).where(claims.c.claim_id.in_(flagged))).mappings())
        exposure=sum(float(c['paid_amount_usd']) if c['claim_status']=='paid' else float(c['allowed_amount_usd']) if c['claim_status']=='pending' else 0 for c in cs)
        trend=[dict(r) for r in con.execute(select(func.to_char(claims.c.service_date,'YYYY-MM').label('month'),func.count().label('claims'),func.sum(claims.c.billed_amount_usd).label('billed'),func.sum(claims.c.paid_amount_usd).label('paid')).group_by('month').order_by('month')).mappings()]
        priorities=[dict(r) for r in con.execute(select(m.cases.c.severity,func.count().label('count')).group_by(m.cases.c.severity)).mappings()]
        latest=con.execute(select(m.batches).where(m.batches.c.kind=='analysis',m.batches.c.status=='COMPLETED').order_by(m.batches.c.created_at.desc()).limit(1)).mappings().first()
        return clean({'counts':counts,'date_range':list(date_range),'active_findings':active,'active_cases':cases_count,'potential_exposure':round(exposure,2),'claims_processed':latest['report'].get('claims_processed',0) if latest else 0,'trend':trend,'priority_distribution':priorities,'exposure_definition':'Unique flagged paid amounts plus pending allowed amounts; potential review exposure, not confirmed loss.','synthetic':True})

@app.get(API+'/dataset/audit',response_model=ObjectResponse)
def audit_summary():
    with engine.connect() as con:
        row=con.execute(select(m.batches).where(m.batches.c.kind=='import').order_by(m.batches.c.created_at.desc()).limit(1)).mappings().first()
        return clean(dict(row)) if row else {'status':'EMPTY','report':{}}

@app.get(API+'/batches',response_model=Page)
def batches(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    with engine.connect() as con:return paginated(con,m.batches,[],page,page_size,m.batches.c.created_at.desc())

@app.get(API+'/batches/{batch_id}',response_model=ObjectResponse)
def batch(batch_id:str):
    with engine.connect() as con:return clean(one(con,m.batches,'batch_id',batch_id))

@app.post(API+'/batches/upload',response_model=ObjectResponse,status_code=202)
async def upload(files:list[UploadFile]=File(...)):
    expected={name+'.csv' for name in m.SPECS};optional={name+'.csv' for name in m.OPTIONAL_SPECS};names=[f.filename for f in files]
    if len(set(names))!=len(names) or not expected<=set(names)<=expected|optional:raise HTTPException(422,'Select all 12 operational CSV files with their original names (optional: provider_profiles.csv, member_profiles.csv). Evaluation labels must not be uploaded.')
    directory=settings.artifact_dir/'uploads'/uuid4().hex;directory.mkdir(parents=True)
    total=0
    for file in files:
        content=await file.read(25*1024*1024+1);total+=len(content)
        if len(content)>25*1024*1024 or total>60*1024*1024:raise HTTPException(413,'Snapshot exceeds upload limit')
        (directory/file.filename).write_bytes(content)
    return enqueue('import',{'directory':str(directory)})

@app.post(API+'/analysis/run',response_model=ObjectResponse,status_code=202)
def run(request:JobRequest):return enqueue(request.kind)

@app.get(API+'/analysis/jobs',response_model=Page)
def jobs():
    with engine.connect() as con:return paginated(con,m.jobs,[],1,30,m.jobs.c.created_at.desc())

@app.get(API+'/analysis/jobs/{job_id}',response_model=ObjectResponse)
def job(job_id:str):
    with engine.connect() as con:return clean(one(con,m.jobs,'job_id',job_id))

@app.get(API+'/claims',response_model=Page)
def claims(q:str=Query('',max_length=100),provider_id:str='',date_from:date|None=None,date_to:date|None=None,has_supplies:bool=False,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
    t=m.tables['claims'];conditions=[]
    if q:conditions.append(or_(t.c.claim_id.ilike(f'%{q}%'),t.c.member_id.ilike(f'%{q}%'),t.c.primary_code.ilike(f'%{q}%')))
    if provider_id:conditions.append(t.c.provider_id==provider_id)
    if date_from:conditions.append(t.c.service_date>=date_from)
    if date_to:conditions.append(t.c.service_date<=date_to)
    if has_supplies:conditions.append(t.c.claim_id.in_(select(m.tables['supply_items'].c.claim_id)))
    with engine.connect() as con:return paginated(con,t,conditions,page,page_size,t.c.service_date.desc())

@app.get(API+'/claims/{claim_id}',response_model=ObjectResponse)
def claim(claim_id:str):
    with engine.connect() as con:
        c=one(con,m.tables['claims'],'claim_id',claim_id);result={'claim':c}
        for name in ['claim_lines','supply_items','claim_estimates']:result[name]=[dict(r) for r in con.execute(select(m.tables[name]).where(m.tables[name].c.claim_id==claim_id)).mappings()]
        result['encounter']=dict(con.execute(select(m.tables['encounters']).where(m.tables['encounters'].c.encounter_id==c['encounter_id'])).mappings().first() or {})
        result['provider']=one(con,m.tables['providers'],'provider_id',c['provider_id'])
        result['facility']=one(con,m.tables['facilities'],'facility_id',c['facility_id'])
        ids=select(m.finding_claims.c.finding_id).where(m.finding_claims.c.claim_id==claim_id)
        result['findings']=get_findings(con,m.findings.c.finding_id.in_(ids))
        result['cases']=list(con.execute(select(m.case_findings.c.case_id).where(m.case_findings.c.finding_id.in_(ids)).distinct()).scalars())
        return clean(result)

@app.get(API+'/claims/{claim_id}/supplytrace',response_model=ObjectResponse)
def supplytrace(claim_id:str):
    result=claim(claim_id);path=settings.artifact_dir/'supply_comparisons.json';comparisons=json.loads(path.read_text()) if path.exists() else {}
    result['comparisons']={s['supply_id']:comparisons.get(s['supply_id'],{'available':False,'reason':'Run analysis to compute peers.'}) for s in result['supply_items']}
    result['limitation']='Itemized supplies are already included in claim lines; do not add them to the bill twice.'
    return result

@app.get(API+'/providers',response_model=Page)
def providers(q:str='',page:int=Query(1,ge=1),page_size:int=Query(100,ge=1,le=300)):
    t=m.tables['providers'];conditions=[or_(t.c.provider_id.ilike(f'%{q}%'),t.c.provider_name.ilike(f'%{q}%'))] if q else []
    with engine.connect() as con:return paginated(con,t,conditions,page,page_size)

@app.get(API+'/providers/{provider_id}',response_model=ObjectResponse)
def provider(provider_id:str):
    with engine.connect() as con:
        p=one(con,m.tables['providers'],'provider_id',provider_id);t=m.tables['claims']
        p['trend']=[dict(r) for r in con.execute(select(func.to_char(t.c.service_date,'YYYY-MM').label('month'),func.count().label('claims'),func.sum(t.c.allowed_amount_usd).label('allowed')).where(t.c.provider_id==provider_id).group_by('month').order_by('month')).mappings()]
        return clean(p)

@app.get(API+'/providers/{provider_id}/anomalies',response_model=ObjectResponse)
def anomalies(provider_id:str):return provider_predictions(provider_id,False)

@app.get(API+'/providers/{provider_id}/forecast',response_model=ObjectResponse)
def forecast(provider_id:str):return provider_predictions(provider_id,True)

def provider_predictions(pid,forecast):
    with engine.connect() as con:
        one(con,m.tables['providers'],'provider_id',pid)
        rows=con.execute(select(m.predictions).where(m.predictions.c.provider_id==pid,m.predictions.c.horizon>0 if forecast else m.predictions.c.horizon==0).order_by(m.predictions.c.created_at.desc())).mappings()
        seen=set();items=[]
        for r in rows:
            if r['horizon'] not in seen:items.append(dict(r));seen.add(r['horizon'])
        return clean({'items':items,'status':'READY' if items else 'UNAVAILABLE','reason':None if items else 'Train models and inspect model status for insufficient data or failures.'})

@app.get(API+'/network/{entity_id}',response_model=ObjectResponse)
def network(entity_id:str,limit:int=Query(70,ge=10,le=150),node_types:str='',relationship_types:str='',as_of:date|None=None):
    from app.services.graph import bounded_network,cached_graph
    with engine.connect() as con:
        def load():
            data=load_data(con);data['successor_links']=[clean(dict(r)) for r in con.execute(select(m.successors).where(m.successors.c.flagged.is_(True))).mappings()];return data
        # Source rows only change through completed imports; successor links only through analyses.
        key=(con.execute(select(func.max(m.batches.c.completed_at)).where(m.batches.c.status=='COMPLETED')).scalar(),con.execute(select(func.count(),func.max(m.successors.c.updated_at)).select_from(m.successors)).one(),settings.db_name)
        result=bounded_network(load() if as_of else None,entity_id,limit,node_types.split(',') if node_types else None,relationship_types.split(',') if relationship_types else None,as_of,None if as_of else cached_graph(key,load))
        if result is None:raise HTTPException(404,'Entity not found')
        result['cases']=[dict(r) for r in con.execute(select(m.cases.c.case_id,m.cases.c.title).join(m.case_entities).where(m.case_entities.c.entity_id==entity_id).limit(20)).mappings()]
        return clean(result)

@app.get(API+'/cases',response_model=Page)
def case_list(q:str='',status:str='',provider_id:str='',severity:str='',type:str='',page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
    conditions=[]
    if q:conditions.append(or_(m.cases.c.title.ilike(f'%{q}%'),m.cases.c.case_id.ilike(f'%{q}%')))
    if status:conditions.append(m.cases.c.case_status==status)
    if provider_id:conditions.append(m.cases.c.primary_entity==provider_id)
    if severity:conditions.append(m.cases.c.severity==severity)
    if type:conditions.append(m.cases.c.case_id.in_(select(m.case_findings.c.case_id).join(m.findings).where(m.findings.c.finding_type==type)))
    with engine.connect() as con:return paginated(con,m.cases,conditions,page,page_size,m.cases.c.priority_score.desc())

@app.get(API+'/siu/queue',response_model=ObjectResponse)
def queue(capacity:int=Query(10,ge=1,le=100),status:str='',provider_id:str='',severity:str='',q:str=''):
    conditions=[~m.cases.c.case_status.in_(['CLOSED','RESOLVED']),m.cases.c.priority_score>0]
    if status:conditions.append(m.cases.c.case_status==status)
    if provider_id:conditions.append(m.cases.c.primary_entity==provider_id)
    if severity:conditions.append(m.cases.c.severity==severity)
    if q:conditions.append(or_(m.cases.c.title.ilike(f'%{q}%'),m.cases.c.case_id.ilike(f'%{q}%')))
    with engine.connect() as con:
        result=paginated(con,m.cases,conditions,1,capacity,m.cases.c.priority_score.desc());result['capacity']=capacity;result['policy_version']='siu-1.0';return result

@app.get(API+'/cases/{case_id}',response_model=ObjectResponse)
def get_case(case_id:str):
    with engine.connect() as con:
        result=case_detail(con,case_id)
        if not result:raise HTTPException(404,'Case not found')
        result['allowed_transitions']=sorted(TRANSITIONS[result['case_status']]);return result

@app.get(API+'/cases/{case_id}/evidence',response_model=ObjectResponse)
def case_evidence(case_id:str):return {'items':get_case(case_id)['evidence']}

@app.get(API+'/cases/{case_id}/timeline',response_model=ObjectResponse)
def timeline(case_id:str):return {'items':get_case(case_id)['timeline']}

@app.post(API+'/cases/{case_id}/actions',response_model=ObjectResponse)
def action(case_id:str,request:ActionInput):
    try:
        with engine.begin() as con:return clean(apply_action(con,case_id,request))
    except LookupError as e:raise HTTPException(404,str(e))
    except ValueError as e:raise HTTPException(422,str(e))

@app.post(API+'/findings/{finding_id}/resolve',response_model=ObjectResponse)
def finding_resolution(finding_id:str,request:ResolutionInput):
    try:
        with engine.begin() as con:return resolve(con,finding_id,request)
    except LookupError as e:raise HTTPException(404,str(e))
    except ValueError as e:raise HTTPException(422,str(e))

@app.post(API+'/cases/{case_id}/brief',response_model=ObjectResponse)
def brief(case_id:str):return make_brief(case_id,'brief')

@app.post(API+'/cases/{case_id}/challenge',response_model=ObjectResponse)
def challenge(case_id:str):return make_brief(case_id,'challenge')

@app.post(API+'/cases/{case_id}/investigate',response_model=ObjectResponse)
def investigate(case_id:str):return make_brief(case_id,'investigate')

def make_brief(case_id,kind):
    from app.services.briefs import compose
    try:return compose(case_id,kind)
    except LookupError as e:raise HTTPException(404,str(e))

@app.get(API+'/models/status',response_model=ObjectResponse)
def model_status():
    with engine.connect() as con:return clean({'items':[dict(r) for r in con.execute(select(m.models).order_by(m.models.c.created_at.desc())).mappings()],'groq':{'configured':bool(settings.groq_api_key),'model':settings.groq_model,'live_availability':'Not checked; use the explicit groq-check command.'}})

@app.get(API+'/evaluation/summary',response_model=ObjectResponse)
def evaluation():
    result={}
    for name,file in [('detection','evaluation_report.json'),('forecast','forecast_report.json'),('controlled_brief_tests','brief_test_report.json'),('scenarios','scenario_report.json')]:
        path=settings.artifact_dir/'evaluation'/file;result[name]=json.loads(path.read_text()) if path.exists() else {'status':'NOT_RUN'}
    return result

# ---- Next-Best-Evidence, evidence requests and simulated member confirmations ----
from contextlib import contextmanager
from app.services import evidence as ev
from app.schemas import EvidenceRequestInput, EvidenceOutcomeInput, SimulationInput, ConfirmationInput, ConfirmationResponseInput

@contextmanager
def read_only():
    """PostgreSQL rejects any write inside these transactions; the connection always rolls back."""
    with engine.connect() as con:
        con.exec_driver_sql('SET TRANSACTION READ ONLY');yield con

def guarded(fn):
    try:return fn()
    except LookupError as e:raise HTTPException(404,str(e))
    except ev.Conflict as e:raise HTTPException(409,str(e))
    except ValueError as e:raise HTTPException(422,str(e))

@app.get(API+'/cases/{case_id}/next-evidence',response_model=ObjectResponse)
def next_evidence(case_id:str,capacity:int=Query(10,ge=1,le=100)):
    with read_only() as con:return guarded(lambda:ev.recommendations(con,case_id,capacity))

@app.get(API+'/siu/queue/next-evidence',response_model=ObjectResponse)
def next_evidence_summary(capacity:int=Query(10,ge=1,le=50)):
    with read_only() as con:return ev.queue_summary(con,capacity)

@app.post(API+'/cases/{case_id}/simulate',response_model=ObjectResponse)
def simulate(case_id:str,request:SimulationInput):
    with read_only() as con:return guarded(lambda:ev.simulate(con,case_id,request.finding_id,request.outcome,request.capacity,request.evidence_type))

@app.get(API+'/cases/{case_id}/evidence-requests',response_model=ObjectResponse)
def evidence_request_list(case_id:str):
    with engine.connect() as con:return {'items':ev.list_requests(con,case_id)}

@app.post(API+'/cases/{case_id}/evidence-requests',response_model=ObjectResponse,status_code=201)
def evidence_request_create(case_id:str,request:EvidenceRequestInput):
    def run():
        with engine.begin() as con:
            if request.evidence_type=='member_confirmation':
                if not request.claim_id:raise ValueError('Member confirmation requests need claim_id')
                return ev.create_confirmation(con,case_id,request.claim_id,request.actor,request.finding_id)
            return ev.create_request(con,case_id,request.finding_id,request.evidence_type,request.actor,request.justification)
    return guarded(run)

@app.post(API+'/evidence-requests/{request_id}/outcome',response_model=ObjectResponse)
def evidence_request_outcome(request_id:str,request:EvidenceOutcomeInput):
    def run():
        with engine.begin() as con:return ev.record_outcome(con,request_id,request.status,request.actor,request.notes)
    return guarded(run)

@app.get(API+'/cases/{case_id}/confirmations',response_model=ObjectResponse)
def confirmation_list(case_id:str):
    with engine.connect() as con:return {'items':ev.list_confirmations(con,case_id),'simulated':True,'notice':'Prototype simulation. No member is ever contacted.'}

@app.post(API+'/cases/{case_id}/confirmations',response_model=ObjectResponse,status_code=201)
def confirmation_create(case_id:str,request:ConfirmationInput):
    def run():
        with engine.begin() as con:return ev.create_confirmation(con,case_id,request.claim_id,request.actor,request.finding_id,request.member_id)
    return guarded(run)

@app.post(API+'/confirmations/{confirmation_id}/response',response_model=ObjectResponse)
def confirmation_response(confirmation_id:str,request:ConfirmationResponseInput):
    def run():
        with engine.begin() as con:return ev.record_response(con,confirmation_id,request.response,request.actor,request.notes)
    return guarded(run)

@app.get(API+'/cases/{case_id}/spillover',response_model=ObjectResponse)
def spillover(case_id:str):
    with engine.connect() as con:
        one(con,m.cases,'case_id',case_id);return ev.spillover(con,case_id)

# ---- Member Radar ----
@app.get(API+'/radar/batches',response_model=Page)
def radar_batch_list(q:str='',qualified:bool|None=None,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
    t=m.radar_batches;conditions=[]
    if q:conditions.append(t.c.provider_id.ilike(f'%{q}%'))
    if qualified is not None:conditions.append(t.c.qualified==qualified)
    with engine.connect() as con:
        total=con.execute(select(func.count()).select_from(t).where(*conditions)).scalar()
        rows=[dict(r) for r in con.execute(select(t,m.tables['providers'].c.provider_name,m.tables['providers'].c.specialty).join(m.tables['providers'],m.tables['providers'].c.provider_id==t.c.provider_id).where(*conditions).order_by(t.c.qualified.desc(),t.c.metrics['growth_ratio'].as_float().desc(),t.c.provider_id).offset((page-1)*page_size).limit(page_size)).mappings()]
        return clean({'items':rows,'total':total,'page':page,'page_size':page_size})

def batch_case(con,finding_id):
    return con.execute(select(m.case_findings.c.case_id).where(m.case_findings.c.finding_id==finding_id).limit(1)).scalar() if finding_id else None

@app.get(API+'/radar/batches/{provider_id}',response_model=ObjectResponse)
def radar_batch(provider_id:str):
    from app.services.radar import CONFIG
    with engine.connect() as con:
        row=one(con,m.radar_batches,'provider_id',provider_id);row['provider']=one(con,m.tables['providers'],'provider_id',provider_id)
        row['case_id']=batch_case(con,row['finding_id']);row['config']={'checks':CONFIG['checks'],'notes':CONFIG['notes'],'version':CONFIG['version']}
        bm=m.radar_batch_members;row['acquisition']=[dict(r) for r in con.execute(select(func.to_char(m.tables['claims'].c.service_date,'YYYY-MM').label('month'),func.count(func.distinct(m.tables['claims'].c.member_id)).label('members')).where(m.tables['claims'].c.provider_id==provider_id).group_by('month').order_by('month')).mappings()]
        return clean(row)

@app.get(API+'/radar/batches/{provider_id}/members',response_model=Page)
def radar_batch_members(provider_id:str,filter:str=Query('',pattern='^(|unrelated|related|context)$'),page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100)):
    t=m.radar_batch_members;conditions=[t.c.provider_id==provider_id]
    if filter=='unrelated':conditions.append(t.c.prior_relationship.is_(None))
    if filter=='related':conditions.append(t.c.prior_relationship.is_not(None))
    if filter=='context':conditions.append(t.c.prior_context.is_(True))
    with engine.connect() as con:
        total=con.execute(select(func.count()).select_from(t).where(*conditions)).scalar()
        rows=[dict(r) for r in con.execute(select(t).where(*conditions).order_by(t.c.member_score.desc().nulls_last(),t.c.member_id).offset((page-1)*page_size).limit(page_size)).mappings()]
        responses=dict(con.execute(select(m.confirmations.c.member_id,m.confirmations.c.response).where(m.confirmations.c.member_id.in_([r['member_id'] for r in rows]))).all())
        for r in rows:r['confirmation']=responses.get(r['member_id'])
        return clean({'items':rows,'total':total,'page':page,'page_size':page_size})

@app.get(API+'/radar/batches/{provider_id}/graph',response_model=ObjectResponse)
def radar_batch_graph(provider_id:str,limit:int=Query(120,ge=10,le=300)):
    with engine.connect() as con:
        batch=one(con,m.radar_batches,'provider_id',provider_id);provider=one(con,m.tables['providers'],'provider_id',provider_id)
        t=m.radar_batch_members;new=[dict(r) for r in con.execute(select(t).where(t.c.provider_id==provider_id).order_by(t.c.member_score.desc().nulls_last(),t.c.member_id).limit(limit)).mappings()]
        claims=m.tables['claims'];existing=list(con.execute(select(claims.c.member_id).where(claims.c.provider_id==provider_id,claims.c.service_date<date.fromisoformat(batch['metrics']['recent_window'][0])).distinct().order_by(claims.c.member_id).limit(max(10,limit//4))).scalars())
        responses=dict(con.execute(select(m.confirmations.c.member_id,m.confirmations.c.response).where(m.confirmations.c.member_id.in_([r['member_id'] for r in new]))).all())
        nodes=[{'data':{'id':provider_id,'label':provider['provider_name'],'type':'provider'}}];edges=[]
        for mid in existing:nodes.append({'data':{'id':mid,'label':mid,'type':'member','member_class':'existing'}});edges.append({'data':{'id':f'{provider_id}-{mid}','source':provider_id,'target':mid,'type':'billed_before_window'}})
        for r in new:
            if r['member_id'] in existing:continue
            response=responses.get(r['member_id'])
            cls={'NO':'simulated_denial','YES':'simulated_confirmation','PENDING':'pending_confirmation','NOT_SURE':'pending_confirmation'}.get(response,'new_unrelated' if r['prior_relationship'] is None else 'new_related')
            nodes.append({'data':{'id':r['member_id'],'label':r['member_id'],'type':'member','member_class':cls,'member_score':r['member_score'],'distance_km':r['distance_km'],'prior_relationship':r['prior_relationship'],'claim_ids':r['claim_ids']}})
            edges.append({'data':{'id':f'{provider_id}-{r["member_id"]}','source':provider_id,'target':r['member_id'],'type':'billed_in_window'}})
        return clean({'nodes':nodes,'edges':edges,'truncated':batch['metrics']['new_members']>limit,'limitation':'Shows recorded billing relationships only; a billing edge is not evidence that a member was harmed or that the provider committed fraud.'})

@app.get(API+'/radar/members',response_model=Page)
def radar_members(page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),q:str=''):
    t=m.member_risk;conditions=[t.c.score.is_not(None)]+([t.c.member_id.ilike(f'%{q}%')] if q else [])
    with engine.connect() as con:return paginated(con,t,conditions,page,page_size,t.c.score.desc())

@app.get(API+'/members/{member_id}/risk',response_model=ObjectResponse)
def member_risk(member_id:str):
    with engine.connect() as con:
        one(con,m.tables['members'],'member_id',member_id)
        row=con.execute(select(m.member_risk).where(m.member_risk.c.member_id==member_id)).mappings().first()
        if not row:return {'member_id':member_id,'status':'UNAVAILABLE','reason':'Run analysis to compute member risk signals.'}
        return clean({**dict(row),'status':'READY','note':'Robust (median/MAD) deviations clipped to 0–5; a descriptive screening score, not a probability that the identity was compromised.'})

# ---- Phoenix Provider Detector ----
def link_rows(con,*conditions):
    p=m.tables['providers'];a=p.alias('pred');b=p.alias('succ');t=m.successors
    rows=[dict(r) for r in con.execute(select(t,a.c.provider_name.label('predecessor_name'),b.c.provider_name.label('successor_name')).join(a,a.c.provider_id==t.c.predecessor_id).join(b,b.c.provider_id==t.c.successor_id).where(*conditions).order_by(t.c.flagged.desc(),t.c.score.desc(),t.c.link_id)).mappings()]
    for r in rows:r['case_id']=batch_case(con,r['finding_id'])
    return clean(rows)

@app.get(API+'/phoenix/links',response_model=ObjectResponse)
def phoenix_links(flagged:bool|None=None):
    from app.services.phoenix import CONFIG
    with engine.connect() as con:return {'items':link_rows(con,*([m.successors.c.flagged==flagged] if flagged is not None else [])),'config':{k:CONFIG[k] for k in ['version','weights','score_threshold','support_threshold','min_supporting_components','notes']}}

@app.get(API+'/phoenix/links/{link_id}',response_model=ObjectResponse)
def phoenix_link(link_id:str):
    with engine.connect() as con:
        rows=link_rows(con,m.successors.c.link_id==link_id)
        if not rows:raise HTTPException(404,'Successor link not found')
        return rows[0]

@app.get(API+'/providers/{provider_id}/successors',response_model=ObjectResponse)
def provider_successors(provider_id:str):
    with engine.connect() as con:one(con,m.tables['providers'],'provider_id',provider_id);return {'items':link_rows(con,m.successors.c.predecessor_id==provider_id)}

@app.get(API+'/providers/{provider_id}/predecessors',response_model=ObjectResponse)
def provider_predecessors(provider_id:str):
    with engine.connect() as con:one(con,m.tables['providers'],'provider_id',provider_id);return {'items':link_rows(con,m.successors.c.successor_id==provider_id)}
