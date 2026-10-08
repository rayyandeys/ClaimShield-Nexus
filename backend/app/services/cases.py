import math
from uuid import uuid4
from sqlalchemy import select, update, or_, and_
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

def case_inputs(con,case_id,lock=False):
    """Exactly the inputs the SIU scorer uses for a case; shared by recalculation and read-only simulation."""
    query=select(cases).where(cases.c.case_id==case_id)
    case=con.execute(query.with_for_update() if lock else query).mappings().first()
    if not case:return None,[],[],0
    fs=[dict(r) for r in con.execute(select(findings).join(case_findings).where(case_findings.c.case_id==case_id).order_by(findings.c.finding_id)).mappings()]
    ids=[f['finding_id'] for f in fs];links={}
    for r in con.execute(select(finding_claims).where(finding_claims.c.finding_id.in_(ids))).mappings():links.setdefault(r['finding_id'],[]).append(r['claim_id'])
    for f in fs:f['claim_ids']=links.get(f['finding_id'],[])
    claim_ids={cid for v in links.values() for cid in v}
    cs=[dict(r) for r in con.execute(select(tables['claims']).where(tables['claims'].c.claim_id.in_(claim_ids))).mappings()]
    forecast=con.execute(select(predictions.c.value).where(predictions.c.provider_id==case['primary_entity'],predictions.c.horizon==90).order_by(predictions.c.created_at.desc()).limit(1)).scalar() or 0
    return dict(case),fs,cs,forecast

# Verified evidence outcomes, applied identically to real reviews and hypothetical simulations.
CORROBORATED_COMPLETENESS=1.0
BATCH_DENIALS_FOR_HIGH=3
def apply_outcome(fs,finding_id,outcome,denials_after=0):
    """Returns a copy of the findings with one verified outcome applied. 'explains' resolves the finding (removed from
    active scoring); 'supports' marks it corroborated: completeness (the scorer's evidence-strength proxy) becomes 1.0 and
    status ESCALATED. A member batch reaches HIGH severity only after three distinct reviewed member denials."""
    out=[dict(f) for f in fs]
    for f in out:
        if f['finding_id']!=finding_id:continue
        if outcome=='explains':f['status']='EXPLAINED'
        elif outcome=='supports':
            f['status']='ESCALATED';f['data_completeness']=max(f['data_completeness'],CORROBORATED_COMPLETENESS)
            if f['finding_type']=='stolen_id_batch' and denials_after>=BATCH_DENIALS_FOR_HIGH:f['severity']='HIGH'
        else:raise ValueError(f'Unknown outcome {outcome}')
    return out

def recalculate(con,case_id,bump=False):
    case,fs,cs,forecast=case_inputs(con,case_id,lock=True)
    values=rank_values(fs,cs,forecast)
    values['updated_at']=now()
    if bump:values['evidence_version']=case['evidence_version']+1
    con.execute(update(cases).where(cases.c.case_id==case_id).values(**values))
    return clean(values)

def consolidate(con,outputs,data):
    import networkx as nx
    graph=nx.Graph();claim_map={c['claim_id']:c for c in data['claims']};month_groups={};provider_level={}
    for f,_ in outputs:
        ids=f.related_claim_ids
        graph.add_nodes_from(ids)
        for cid in ids[1:]:graph.add_edge(ids[0],cid)
        # Provider-level findings about the same provider (anomaly, member batch, possible successor) form one investigation.
        if f.entity_type=='provider' and ids:
            if f.entity_id in provider_level:graph.add_edge(ids[0],provider_level[f.entity_id])
            else:provider_level[f.entity_id]=ids[0]
        for cid in ids:
            c=claim_map[cid];key=(c['provider_id'],str(c['service_date'])[:7])
            if key in month_groups:graph.add_edge(cid,month_groups[key])
            else:month_groups[key]=cid
    count=0
    for component in nx.connected_components(graph):
        claim_ids=sorted(component);cs=[claim_map[k] for k in claim_ids]
        fs=[f for f,_ in outputs if component.intersection(f.related_claim_ids)]
        # A component whose findings already belong to a case (e.g. linked incrementally by the live stream) keeps that
        # case ID; otherwise the stable ID of its first claim. On a fresh database this is identical to the stable ID.
        prior=con.execute(select(cases.c.case_id).join(case_findings).where(case_findings.c.finding_id.in_([f.finding_id for f in fs])).order_by(cases.c.created_at,cases.c.case_id).limit(1)).scalar()
        cid=prior or stable_id('CASE-',claim_ids[0])
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

def corroborate(con,finding_id,actor,explanation,evidence_ids,denials_after=0):
    """Persist a reviewed supporting outcome via apply_outcome, then recalculate every affected case and audit it."""
    f=con.execute(select(findings).where(findings.c.finding_id==finding_id).with_for_update()).mappings().first()
    if not f:raise LookupError('Finding not found')
    updated=apply_outcome([dict(f)],finding_id,'supports',denials_after)[0]
    before={k:f[k] for k in ['status','severity','data_completeness']};after={k:updated[k] for k in before}
    con.execute(update(findings).where(findings.c.finding_id==finding_id).values(**after))
    affected=list(con.execute(select(case_findings.c.case_id).where(case_findings.c.finding_id==finding_id)).scalars())
    for cid in affected:
        ranking=recalculate(con,cid,bump=True)
        record_action(con,cid,actor,'finding_corroborated',explanation,before,{**after,'verified_evidence_ids':evidence_ids,'ranking':ranking},finding_id)
    return {'finding_id':finding_id,'status':after['status'],'recalculated_cases':affected}

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

def link_incremental(con,outputs,actor='Live stream processor'):
    """Incremental consolidation for findings persisted outside a full analysis (live stream). Uses the same linking rules
    as consolidate(): shared claims, provider-level findings about the same provider, and the same provider-month.
    A component that touches existing open cases joins the highest-priority one; existing case IDs are never changed or
    merged. Otherwise a case is created with the same stable ID scheme. Findings already linked to a case only trigger a
    recalculation (idempotent retries). Returns {'created','updated','finding_cases','rankings'}."""
    import networkx as nx
    from sqlalchemy import func, or_, and_, tuple_
    claims_t=tables['claims']
    fids=[f.finding_id for f,_ in outputs]
    linked={}
    for r in con.execute(select(case_findings.c.finding_id,case_findings.c.case_id).where(case_findings.c.finding_id.in_(fids))).all():linked.setdefault(r[0],[]).append(r[1])
    claim_ids={cid for f,_ in outputs for cid in f.related_claim_ids}
    claim_map={c['claim_id']:dict(c) for c in con.execute(select(claims_t).where(claims_t.c.claim_id.in_(claim_ids))).mappings()}
    fresh=[f for f,_ in outputs if f.finding_id not in linked]
    graph=nx.Graph();month_groups={};provider_level={}
    for f in fresh:
        ids=f.related_claim_ids;graph.add_nodes_from(ids)
        for cid in ids[1:]:graph.add_edge(ids[0],cid)
        if f.entity_type=='provider' and ids:
            if f.entity_id in provider_level:graph.add_edge(ids[0],provider_level[f.entity_id])
            else:provider_level[f.entity_id]=ids[0]
        for cid in ids:
            c=claim_map[cid];key=(c['provider_id'],str(c['service_date'])[:7])
            if key in month_groups:graph.add_edge(cid,month_groups[key])
            else:month_groups[key]=cid
    created,updated,finding_cases=[],[],{k:v[0] for k,v in linked.items()}
    touched=set(cid for v in linked.values() for cid in v)
    for f,_ in outputs:
        for cid in linked.get(f.finding_id,[]):
            for eid in {v for k in f.related_claim_ids if k in claim_map for v in [k,claim_map[k]['provider_id'],claim_map[k]['member_id'],claim_map[k]['facility_id']]}:con.execute(insert(case_entities).values(case_id=cid,entity_id=eid).on_conflict_do_nothing())
    for component in nx.connected_components(graph):
        fs=[f for f in fresh if component.intersection(f.related_claim_ids)]
        months={(claim_map[c]['provider_id'],str(claim_map[c]['service_date'])[:7]) for c in component}
        entities_=[f.entity_id for f in fs if f.entity_type=='provider']
        conditions=[finding_claims.c.claim_id.in_(sorted(component))]
        if entities_:conditions.append(and_(findings.c.entity_type=='provider',findings.c.entity_id.in_(entities_)))
        conditions.append(tuple_(claims_t.c.provider_id,func.to_char(claims_t.c.service_date,'YYYY-MM')).in_(sorted(months)))
        candidates=[dict(r) for r in con.execute(select(cases.c.case_id,cases.c.case_status,cases.c.priority_score).distinct().select_from(cases.join(case_findings).join(findings,findings.c.finding_id==case_findings.c.finding_id).join(finding_claims,finding_claims.c.finding_id==findings.c.finding_id).join(claims_t,claims_t.c.claim_id==finding_claims.c.claim_id)).where(or_(*conditions))).mappings()]
        open_cases=sorted([c for c in candidates if c['case_status'] not in {'CLOSED','RESOLVED'}],key=lambda c:(-c['priority_score'],c['case_id']))
        cs=[claim_map[k] for k in sorted(component)];types=sorted({f.finding_type for f in fs})
        if open_cases:
            cid=open_cases[0]['case_id'];updated.append(cid);note=f'{len(fs)} new screening finding(s) ({", ".join(types)}) linked from live-stream claims.'
            if len(open_cases)>1:note+=f' Also related to open case(s) {", ".join(c["case_id"] for c in open_cases[1:])}; cases are not merged automatically.'
            con.execute(audit.insert().values(event_id='EV-'+uuid4().hex,case_id=cid,actor=actor,action_type='findings_linked',explanation=note,previous_state={},new_state=clean({'finding_ids':[f.finding_id for f in fs],'claim_ids':sorted(component)})))
        else:
            cid=stable_id('CASE-',sorted(component)[0]);provider=cs[0]['provider_id']
            exists=con.execute(select(cases.c.case_id).where(cases.c.case_id==cid)).scalar()
            if not exists:
                con.execute(cases.insert().values(case_id=cid,title=f'{provider} · {types[0].replace("_"," ")}',summary=f'{len(fs)} screening findings across {len(cs)} claims. Review source records and possible legitimate explanations.',primary_entity=provider,severity='MEDIUM',priority_score=0,potential_financial_exposure=0,evidence_strength=0,member_impact=0,payment_workflow='MIXED' if {c['claim_status'] for c in cs}>={'paid','pending'} else 'POSTPAYMENT' if any(c['claim_status']=='paid' for c in cs) else 'PREPAYMENT',case_status='NEW',evidence_version=1))
                note='Consolidated live-stream screening findings.'+(f' Related closed/resolved case(s) {", ".join(c["case_id"] for c in candidates)} were not reopened automatically.' if candidates else '')
                con.execute(audit.insert().values(event_id='EV-'+uuid4().hex,case_id=cid,actor=actor,action_type='case_created',explanation=note,previous_state={},new_state={'case_status':'NEW'}))
                created.append(cid)
            else:updated.append(cid)
        for f in fs:
            con.execute(insert(case_findings).values(case_id=cid,finding_id=f.finding_id).on_conflict_do_nothing());finding_cases[f.finding_id]=cid
        for eid in {v for c in cs for v in [c['claim_id'],c['provider_id'],c['member_id'],c['facility_id']]}:con.execute(insert(case_entities).values(case_id=cid,entity_id=eid).on_conflict_do_nothing())
        touched.add(cid)
    rankings={cid:recalculate(con,cid) for cid in sorted(touched)}
    return {'created':created,'updated':sorted(set(updated)-set(created)),'finding_cases':finding_cases,'rankings':rankings}

def queue_position(con,case_id):
    """1-based rank among open, scored cases (the SIU queue ordering), or None when the case is not in the queue."""
    from sqlalchemy import func
    row=con.execute(select(cases.c.priority_score,cases.c.case_status).where(cases.c.case_id==case_id)).first()
    if not row or row[1] in {'CLOSED','RESOLVED'} or not row[0]:return None
    ahead=con.execute(select(func.count()).select_from(cases).where(~cases.c.case_status.in_(['CLOSED','RESOLVED']),cases.c.priority_score>0,or_(cases.c.priority_score>row[0],and_(cases.c.priority_score==row[0],cases.c.case_id<case_id)))).scalar()
    return ahead+1
