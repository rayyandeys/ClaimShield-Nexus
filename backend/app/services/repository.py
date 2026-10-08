from collections import defaultdict
from sqlalchemy import select, func
from app.models import tables, findings, finding_claims, evidence, case_findings, cases, case_entities, predictions, audit
from app.core import clean
from app.schemas import Finding

def load_data(con):
    return {name:[dict(r) for r in con.execute(select(t)).mappings()] for name,t in tables.items()}

def get_findings(con, condition=None):
    query=select(findings)
    if condition is not None: query=query.where(condition)
    rows=[dict(r) for r in con.execute(query.order_by(findings.c.created_at.desc())).mappings()]
    if not rows:return []
    ids=[r['finding_id'] for r in rows]; claim_map=defaultdict(list); evidence_map=defaultdict(list)
    joined=finding_claims.join(tables['claims'],finding_claims.c.claim_id==tables['claims'].c.claim_id)
    for r in con.execute(select(finding_claims.c.finding_id,tables['claims']).select_from(joined).where(finding_claims.c.finding_id.in_(ids))).mappings(): claim_map[r['finding_id']].append(r)
    for r in con.execute(select(evidence.c.finding_id,evidence.c.evidence_id).where(evidence.c.finding_id.in_(ids))).mappings(): evidence_map[r['finding_id']].append(r['evidence_id'])
    out=[]
    for r in rows:
        links=claim_map[r['finding_id']]
        r['related_claim_ids']=[c['claim_id'] for c in links]
        r['related_provider_ids']=sorted({c['provider_id'] for c in links})
        r['related_facility_ids']=sorted({c['facility_id'] for c in links})
        r['evidence_ids']=evidence_map[r['finding_id']]
        r['detected_at']=r.pop('created_at');r['anomaly_score_or_rule_result']=r.pop('score');r.pop('batch_id')
        out.append(Finding.model_validate(r).model_dump(mode='json'))
    return out

def case_detail(con,case_id):
    case=con.execute(select(cases).where(cases.c.case_id==case_id)).mappings().first()
    if not case:return None
    result=dict(case)
    fids=select(case_findings.c.finding_id).where(case_findings.c.case_id==case_id)
    result['findings']=get_findings(con,findings.c.finding_id.in_(fids))
    claim_ids=sorted({cid for f in result['findings'] for cid in f['related_claim_ids']})
    result['claims']=[dict(r) for r in con.execute(select(tables['claims']).where(tables['claims'].c.claim_id.in_(claim_ids))).mappings()]
    result['evidence']=[dict(r) for r in con.execute(select(evidence).where(evidence.c.finding_id.in_(fids))).mappings()]
    result['related_entities']=list(con.execute(select(case_entities.c.entity_id).where(case_entities.c.case_id==case_id)).scalars())
    result['forecast']=[dict(r) for r in con.execute(select(predictions).where(predictions.c.provider_id==case['primary_entity'],predictions.c.horizon>0).order_by(predictions.c.created_at.desc())).mappings()][:3]
    result['timeline']=[dict(r) for r in con.execute(select(audit).where(audit.c.case_id==case_id).order_by(audit.c.created_at.desc())).mappings()]
    result['financials']={key:round(sum(float(c[key]) for c in result['claims']),2) for key in ['billed_amount_usd','allowed_amount_usd','paid_amount_usd','member_responsibility_usd']}
    result['financials']['confirmed_recoverable_overpayment']=None
    result['financials']['exposure_definition']='Paid amounts on active flagged paid claims plus allowed amounts on active pending claims; upper-bound review exposure, not estimated fraud loss.'
    return clean(result)
