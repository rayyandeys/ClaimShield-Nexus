import math
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from app.core import clean, now
from app.models import cases, findings, finding_claims, case_findings, case_entities, evidence, predictions, audit, actions, assignments, tables
from app.services.detection import stable_id

ACTIVE={'ACTIVE','UNRESOLVED','ESCALATED'}
TRANSITIONS={'NEW':{'QUEUED','UNDER_REVIEW','CLOSED'},'QUEUED':{'UNDER_REVIEW','CLOSED'},'UNDER_REVIEW':{'AWAITING_EVIDENCE','ESCALATED','RESOLVED'},'AWAITING_EVIDENCE':{'UNDER_REVIEW','ESCALATED','RESOLVED'},'ESCALATED':{'UNDER_REVIEW','RESOLVED'},'RESOLVED':{'UNDER_REVIEW','CLOSED'},'CLOSED':{'UNDER_REVIEW'}}

def record_action(con,case_id,actor,kind,explanation,before,after,finding_id=None):
    payload=dict(case_id=case_id,finding_id=finding_id,actor=actor,action_type=kind,explanation=explanation,previous_state=clean(before),new_state=clean(after))
    con.execute(actions.insert().values(action_id='A-'+uuid4().hex,**payload))
    con.execute(audit.insert().values(event_id='EV-'+uuid4().hex,**payload))

def rank_values(fs,cs,forecast=0):
    active=[f for f in fs if f['status'] in ACTIVE]
    active_ids={cid for f in active for cid in f.get('claim_ids',[])}
    related={c['claim_id']:c for c in cs if c['claim_id'] in active_ids}
    exposure=sum(float(c['paid_amount_usd']) if c['claim_status']=='paid' else float(c['allowed_amount_usd']) if c['claim_status']=='pending' else 0 for c in related.values())
    severity=max(({'LOW':.25,'MEDIUM':.6,'HIGH':1}[f['severity']] for f in active),default=0)
    strength=sum(f['data_completeness'] for f in active)/max(len(active),1)
    channels=len({f['engine'] for f in active});members=len({c['member_id'] for c in related.values()})
    factors={'severity':30*severity,'evidence':20*strength if active else 0,'exposure':20*min(math.log1p(exposure)/math.log1p(100000),1),'member_impact':10*min(members/10,1),'recurrence':10*forecast if active else 0,'independent_channels':10*min(max(channels-1,0)/3,1)}
    return {'priority_score':round(sum(factors.values()),2),'potential_financial_exposure':round(exposure,2),'evidence_strength':round(strength,3),'member_impact':members,'severity':'HIGH' if severity==1 else 'MEDIUM' if severity else 'LOW','ranking':{'policy_version':'siu-1.0','factors':{k:round(v,2) for k,v in factors.items()},'active_findings':len(active),'insufficient_evidence':strength<.75,'exposure_definition':'Unique active-flagged paid amounts + pending allowed amounts; review upper bound, not confirmed loss.','forecast_missing':not forecast,'probability_of_fraud':False}}

def recalculate(con,case_id,bump=False):
    case=con.execute(select(cases).where(cases.c.case_id==case_id).with_for_update()).mappings().one()
    fs=[dict(r) for r in con.execute(select(findings).join(case_findings).where(case_findings.c.case_id==case_id)).mappings()]
    ids=[f['finding_id'] for f in fs];links={}
    for r in con.execute(select(finding_claims).where(finding_claims.c.finding_id.in_(ids))).mappings():links.setdefault(r['finding_id'],[]).append(r['claim_id'])
    for f in fs:f['claim_ids']=links.get(f['finding_id'],[])
    claim_ids={cid for v in links.values() for cid in v}
    cs=[dict(r) for r in con.execute(select(tables['claims']).where(tables['claims'].c.claim_id.in_(claim_ids))).mappings()]
    forecast=con.execute(select(predictions.c.value).where(predictions.c.provider_id==case['primary_entity'],predictions.c.horizon==90).order_by(predictions.c.created_at.desc()).limit(1)).scalar() or 0
    values=rank_values(fs,cs,forecast)
    values['updated_at']=now()
    if bump:values['evidence_version']=case['evidence_version']+1
    con.execute(update(cases).where(cases.c.case_id==case_id).values(**values))
    return clean(values)

def consolidate(con,outputs,data):
    import networkx as nx
    graph=nx.Graph();claim_map={c['claim_id']:c for c in data['claims']};month_groups={}
    for f,_ in outputs:
        ids=f.related_claim_ids
        graph.add_nodes_from(ids)
        for cid in ids[1:]:graph.add_edge(ids[0],cid)
        for cid in ids:
            c=claim_map[cid];key=(c['provider_id'],str(c['service_date'])[:7])
            if key in month_groups:graph.add_edge(cid,month_groups[key])
            else:month_groups[key]=cid
    count=0
    for component in nx.connected_components(graph):
        claim_ids=sorted(component);cid=stable_id('CASE-',claim_ids[0]);cs=[claim_map[k] for k in claim_ids]
        fs=[f for f,_ in outputs if component.intersection(f.related_claim_ids)]
        provider=cs[0]['provider_id'];types=sorted({f.finding_type for f in fs})
        values=dict(case_id=cid,title=f'{provider} · {types[0].replace("_"," ")}',summary=f'{len(fs)} screening findings across {len(cs)} claims. Review source records and possible legitimate explanations.',primary_entity=provider,severity='MEDIUM',priority_score=0,potential_financial_exposure=0,evidence_strength=0,member_impact=0,payment_workflow='MIXED' if {c['claim_status'] for c in cs}>={'paid','pending'} else 'POSTPAYMENT' if any(c['claim_status']=='paid' for c in cs) else 'PREPAYMENT')
        existing=con.execute(select(cases.c.case_id).where(cases.c.case_id==cid)).scalar()
        if not existing:con.execute(cases.insert().values(**values,case_status='NEW',evidence_version=1))
        for f in fs:con.execute(insert(case_findings).values(case_id=cid,finding_id=f.finding_id).on_conflict_do_nothing())
        ids={v for c in cs for v in [c['claim_id'],c['provider_id'],c['member_id'],c['facility_id']]}
        for eid in ids:con.execute(insert(case_entities).values(case_id=cid,entity_id=eid).on_conflict_do_nothing())
        recalculate(con,cid)
        if not existing:con.execute(audit.insert().values(event_id='EV-'+uuid4().hex,case_id=cid,actor='Analysis worker',action_type='case_created',explanation='Consolidated shared-claim and provider-month screening findings.',previous_state={},new_state={'case_status':'NEW'}))
        count+=1
    return count

def apply_action(con,case_id,request):
    case=con.execute(select(cases).where(cases.c.case_id==case_id).with_for_update()).mappings().first()
    if not case:raise LookupError('Case not found')
    before={'case_status':case['case_status'],'assigned_investigator':case['assigned_investigator']};after=dict(before)
    if request.action_type=='status':
        if request.value not in TRANSITIONS[case['case_status']]:raise ValueError(f'Invalid transition from {case["case_status"]} to {request.value}')
        after['case_status']=request.value
    elif request.action_type=='assign':
        if not request.value.strip():raise ValueError('Investigator name is required')
        after['assigned_investigator']=request.value
        con.execute(assignments.insert().values(assignment_id='AS-'+uuid4().hex,case_id=case_id,investigator=request.value))
    elif request.action_type=='request_records':
        if 'AWAITING_EVIDENCE' not in TRANSITIONS[case['case_status']]:raise ValueError('Open the case for review before requesting records')
        after['case_status']='AWAITING_EVIDENCE'
    con.execute(update(cases).where(cases.c.case_id==case_id).values(**after,updated_at=now(),evidence_version=case['evidence_version']+1))
    record_action(con,case_id,request.actor,request.action_type,request.explanation,before,after)
    return after

def resolve(con,finding_id,request):
    f=con.execute(select(findings).where(findings.c.finding_id==finding_id).with_for_update()).mappings().first()
    if not f:raise LookupError('Finding not found')
    valid=set(con.execute(select(evidence.c.evidence_id).where(evidence.c.finding_id==finding_id)).scalars())
    if not set(request.verified_evidence_ids)<=valid:raise ValueError('Verified evidence must belong to this finding')
    con.execute(update(findings).where(findings.c.finding_id==finding_id).values(status=request.status))
    affected=list(con.execute(select(case_findings.c.case_id).where(case_findings.c.finding_id==finding_id)).scalars())
    for cid in affected:
        ranking=recalculate(con,cid,bump=True)
        record_action(con,cid,request.actor,'finding_resolution',request.explanation,{'status':f['status']},{'status':request.status,'verified_evidence_ids':request.verified_evidence_ids,'ranking':ranking},finding_id)
    return {'finding_id':finding_id,'status':request.status,'recalculated_cases':affected}
