"""Evidence-producing rules and robust itemized billing comparisons."""
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import numpy as np
from app.core import clean
from app.schemas import Evidence, Finding

VERSION = 'synthetic-policy-1.0'

def stable_id(prefix, *parts):
    return prefix + hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:20]

def make_finding(kind, claims, sources, explanation, severity='MEDIUM', score=1., engine='rules', context=None, limitations=None, entity=None, version=VERSION, completeness=1.):
    claims = sorted(claims, key=lambda c:c['claim_id'])
    entity_id = entity or claims[0]['claim_id']
    fid = stable_id('F-', engine, kind, entity_id, *[r[1] for r in sources])
    evidence = []
    for table, source_id, record in sources:
        timestamp = record.get('service_start') or record.get('referral_date') or record.get('effective_from')
        if timestamp and not isinstance(timestamp, datetime): timestamp = datetime.combine(timestamp, datetime.min.time(), tzinfo=timezone.utc)
        evidence.append(Evidence(evidence_id=stable_id('E-', fid, table, source_id), finding_id=fid, source_table_or_type=table, source_record_id=source_id, evidence_type=kind, observed_value=clean(record), reference_value_or_context=clean(context or {}), record_timestamp=timestamp, provenance={'source_file':record.get('source_file',f'{table}.csv'), 'source_batch_id':record.get('source_batch_id'), 'synthetic':True}))
    finding = Finding(finding_id=fid, finding_type=kind, engine=engine, entity_type='provider' if entity else 'claim', entity_id=entity_id, related_claim_ids=[c['claim_id'] for c in claims], related_provider_ids=sorted({c['provider_id'] for c in claims}), related_facility_ids=sorted({c['facility_id'] for c in claims}), severity=severity, anomaly_score_or_rule_result=float(score), rule_or_model_version=version, evidence_ids=[e.evidence_id for e in evidence], explanation=explanation, data_completeness=completeness, limitations=limitations or ['Synthetic screening indicator; human verification required.'], detected_at=datetime.now(timezone.utc), status='ACTIVE')
    return finding, evidence

def policy_for(data, rule, claim):
    return next((p for p in data['billing_policies'] if p['rule_type']==rule and claim['claim_type'] in p['applicable_claim_types'].split(',') and p['effective_from'] <= claim['service_date'] <= p['effective_to']), None)

def rules(data):
    output=[]; claims=data['claims']; encounters={e['encounter_id']:e for e in data['encounters']}
    line_map=defaultdict(list)
    for line in data['claim_lines']: line_map[line['claim_id']].append(line)
    duplicates=defaultdict(list)
    for c in claims:
        # Corrections AND replaced originals are excluded from duplicate comparisons.
        duplicates[(c['member_id'],c['provider_id'],c['service_date'],c['primary_code'],c['service_start'])].append(c)
    corrected={c['correction_of_claim_id'] for c in claims if c.get('correction_of_claim_id')}
    for group in duplicates.values():
        eligible=[c for c in group if c['claim_id'] not in corrected and not c.get('correction_of_claim_id') and c['claim_status'] not in {'denied','void','reversed','replaced','corrected'}]
        if len(eligible)>1 and policy_for(data,'duplicate_billing',eligible[0]):
            output.append(make_finding('duplicate_billing',eligible,[('claims',c['claim_id'],c) for c in eligible],f'{len(eligible)} separate submissions share member, provider, procedure and service timestamp. No correction link is recorded.','HIGH', limitations=['Verify adjustment and replacement records before concluding duplicate payment.']))
    for c in claims:
        e=encounters.get(c['encounter_id']); sources=[('claims',c['claim_id'],c)]
        if e: sources.append(('encounters',e['encounter_id'],e))
        if not e and policy_for(data,'missing_encounter_indicator',c):
            output.append(make_finding('missing_encounter_indicator',[c],sources,'No matching encounter is available in the imported scheduling records.',completeness=.5,limitations=['Incomplete scheduling data may explain the absence; this is a documentation gap, not proof of phantom service.']))
        if e and c['primary_code']=='SIM-CONSULT-L3' and e['documented_code']=='SIM-CONSULT-L1' and e['documented_complexity']=='low' and policy_for(data,'upcoding_indicator',c):
            output.append(make_finding('upcoding_indicator',[c],sources,'Billed synthetic level-3 consultation conflicts with a documented low-complexity level-1 encounter.','HIGH',limitations=['Synthetic code hierarchy only. Clinical documentation must be reviewed.']))
        codes={l['procedure_code'] for l in line_map[c['claim_id']]}
        if {'SIM-PACKAGE-BASE','SIM-PACKAGE-COMP'} <= codes and policy_for(data,'unbundling_indicator',c):
            ls=[('claim_lines',l['claim_line_id'],l) for l in line_map[c['claim_id']] if l['procedure_code'] in {'SIM-PACKAGE-BASE','SIM-PACKAGE-COMP'}]
            p=policy_for(data,'unbundling_indicator',c)
            output.append(make_finding('unbundling_indicator',[c],sources+ls+[('billing_policies',p['policy_id'],p)],'Package component is separately billed with its base under the imported synthetic bundling policy.','HIGH',limitations=[p['limitation']]))
        for l in line_map[c['claim_id']]:
            if l.get('days_supply') and l.get('days_since_last_fill') is not None and l['days_since_last_fill'] < .5*l['days_supply'] and policy_for(data,'early_refill',c):
                output.append(make_finding('early_refill',[c],sources+[('claim_lines',l['claim_line_id'],l)],'Recorded refill interval is below half of the previous days supply.',context={'minimum_fraction':.5},limitations=['Lost medication, dose change or travel may justify an early refill.']))
    # Documented encounters provide the timing source; one claim per encounter avoids duplicate alert inflation.
    encounter_claim={c['encounter_id']:c for c in claims if c['encounter_id']}
    schedules=defaultdict(list)
    for e in data['encounters']:
        c=encounter_claim.get(e['encounter_id'])
        if c and policy_for(data,'impossible_timing',c): schedules[e['provider_id']].append((e,c))
    for schedule in schedules.values():
        active=[]
        for e,c in sorted(schedule,key=lambda pair:pair[0]['service_start']):
            active=[(old,oc) for old,oc in active if old['service_start']+timedelta(minutes=old['duration_min'])>e['service_start']]
            for old,oc in active:
                if old['member_id'] != e['member_id']:
                    output.append(make_finding('impossible_timing',[oc,c],[('encounters',old['encounter_id'],old),('encounters',e['encounter_id'],e)],'Documented appointments for different members overlap for the same provider.','HIGH',limitations=['Synthetic timestamps assumed UTC; team-based staffing or scheduling errors can explain overlap.']))
            active.append((e,c))
    # Rolling utilization compared within procedure and complexity; minimum 20 peer observations.
    groups=defaultdict(list); peer_counts=defaultdict(list); observations=[]
    for c in sorted(claims,key=lambda c:c['service_date']):
        key=(c['member_id'],c['primary_code']); group=groups[key]
        group[:]=[old for old in group if (c['service_date']-old['service_date']).days <= 30]
        group.append(c); peer=(c['primary_code'],c['complexity_band'])
        observations.append((c,len(group),peer,list(group))); peer_counts[peer].append(len(group))
    for c,count,peer,group in observations:
        vals=peer_counts[peer]
        if len(vals)>=20:
            q1,q3=np.quantile(vals,[.25,.75]); threshold=max(5,float(q3+3*max(q3-q1,1)))
            if count>threshold and c['complexity_band']!='high':
                output.append(make_finding('excessive_utilization',group,[('claims',g['claim_id'],g) for g in group],f'{count} same-procedure services in 30 days exceed the procedure/complexity peer threshold of {threshold:.1f}.',context={'rolling_days':30,'peer_threshold':threshold},limitations=['Treatment plans and clinical necessity are unavailable; high-complexity cases are excluded from this conservative rule.']))
    return output

def supply_analysis(data):
    claims={c['claim_id']:c for c in data['claims']}; peers=defaultdict(list); output=[]; comparisons={}
    for s in data['supply_items']:
        c=claims[s['claim_id']]; peers[(c['primary_code'],c['complexity_band'],s['supply_code'])].append(s)
    duplicates=defaultdict(list)
    for s in data['supply_items']:
        c=claims[s['claim_id']]; group=peers[(c['primary_code'],c['complexity_band'],s['supply_code'])]
        ctx={'procedure':c['primary_code'],'complexity':c['complexity_band'],'peer_count':len(group),'benign_explanations':['Additional procedure or complication','Documented treatment complexity','Packaging or unit-of-measure difference'], 'missing_documentation':['Operative note','Item usage log','Contracted unit price']}
        for field in ['quantity','unit_charge_usd']:
            values=np.array([float(g[field]) for g in group]); q1,median,q3=np.quantile(values,[.25,.5,.75]); iqr=q3-q1
            threshold=float(q3+3*max(iqr,median*.1,.01))
            ctx[field]={'median':float(median),'q1':float(q1),'q3':float(q3),'threshold':threshold,'observed':float(s[field]),'robust_deviation':float((float(s[field])-median)/max(iqr,.01)),'available':len(group)>=20}
            if len(group)>=20 and float(s[field])>threshold and policy_for(data,'consumable_quantity_anomaly',c):
                kind='consumable_quantity_anomaly' if field=='quantity' else 'supply_unit_price_anomaly'
                output.append(make_finding(kind,[c],[('supply_items',s['supply_id'],s),('claims',c['claim_id'],c)],f'{s["supply_description"]}: {field.replace("_"," ")} {float(s[field]):.2f} exceeds matched-peer threshold {threshold:.2f}.','HIGH',float(s[field])/max(threshold,.01),'supplytrace',context=ctx,limitations=['Peer group matches procedure and recorded complexity; unrecorded additional services may explain excess.','Supply items already appear in claim lines and must not be added again to billed totals.']))
        comparisons[s['supply_id']]=clean(ctx)
        duplicates[(s['claim_id'],s['supply_code'],s['quantity'],s['unit_charge_usd'])].append(s)
    for group in duplicates.values():
        if len(group)>1:
            c=claims[group[0]['claim_id']]
            output.append(make_finding('duplicate_supply_charge',[c],[('supply_items',s['supply_id'],s) for s in group],'Multiple distinct supply records share item, quantity and price.',engine='supplytrace',limitations=['Repeated item usage may be legitimate; verify service and item logs.']))
    for e in data['claim_estimates']:
        c=claims[e['claim_id']]; ratio=float(e['difference_usd'])/max(float(e['estimated_billed_usd']),1)
        if ratio>.5 and policy_for(data,'estimate_to_final_drift',c):
            output.append(make_finding('estimate_to_final_drift',[c],[('claim_estimates',e['estimate_id'],e),('claims',c['claim_id'],c)],f'Final billed amount exceeds the documented estimate by {ratio:.0%}.',score=ratio,engine='supplytrace',context={'relative_increase':ratio,'threshold':.5},limitations=['Estimate is not a binding price. Additional procedures and complications require review.']))
    return output,comparisons
