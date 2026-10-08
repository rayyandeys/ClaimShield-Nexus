import csv
import json
import logging
import time
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from app.core import engine, settings, clean, now
from sqlalchemy import delete
from app.models import batches, findings, evidence, finding_claims, models, cases, case_findings, member_risk, radar_batches, radar_batch_members, successors
from app.services.repository import load_data
from app.services.detection import rules, supply_analysis
from app.services.graph import graph_analysis
from app.services import radar, phoenix
from app.services.ml import train_anomaly, forecast_training
from app.services.cases import consolidate

log=logging.getLogger(__name__)

def persist_detector_tables(con,batch_id,member_rows,batch_rows,batch_member_rows,link_rows):
    """Radar tables are derived snapshots and are replaced; successor links are upserted by stable pair ID."""
    for start in range(0,len(member_rows),1000):
        stmt=insert(member_risk).values([clean(dict(r,batch_id=batch_id)) for r in member_rows[start:start+1000]])
        con.execute(stmt.on_conflict_do_update(index_elements=['member_id'],set_={k:stmt.excluded[k] for k in ['score','as_of','signals','detector_version','batch_id']}))
    con.execute(delete(radar_batch_members));con.execute(delete(radar_batches))
    if batch_rows:con.execute(radar_batches.insert(),[clean(dict(r,batch_id=batch_id)) for r in batch_rows])
    for start in range(0,len(batch_member_rows),1000):con.execute(radar_batch_members.insert(),[clean(r) for r in batch_member_rows[start:start+1000]])
    for r in link_rows:
        stmt=insert(successors).values(**clean(dict(r,batch_id=batch_id)))
        con.execute(stmt.on_conflict_do_update(index_elements=['link_id'],set_={**{k:stmt.excluded[k] for k in ['score','flagged','components','details','detector_version','batch_id']},'finding_id':func.coalesce(stmt.excluded.finding_id,successors.c.finding_id),'updated_at':func.now()}))

def scenario_evaluation(batch_rows,link_rows):
    """Compares detector output with the separate scenario manifest (labels never reach detection)."""
    path=settings.artifact_dir/'scenarios/v1/scenario_manifest.json'
    if not path.exists():return {'status':'NOT_AVAILABLE','reason':'Scenario pack manifest not found; run the augment command to evaluate scenarios.'}
    manifest=json.loads(path.read_text());qualified={b['provider_id'] for b in batch_rows if b['qualified']};flagged={(l['predecessor_id'],l['successor_id']) for l in link_rows if l['flagged']}
    radar_rows=[dict(s,detected=s['provider_id'] in qualified,correct=(s['provider_id'] in qualified)==s['expected_flag']) for s in manifest['radar']]
    phoenix_rows=[dict(s,detected=(s['predecessor_id'],s['successor_id']) in flagged,correct=((s['predecessor_id'],s['successor_id']) in flagged)==s['expected_flag']) for s in manifest['phoenix']]
    return {'status':'CALCULATED','version':manifest['version'],'radar':radar_rows,'phoenix':phoenix_rows,'unexpected_radar_flags':sorted(qualified-{s['provider_id'] for s in manifest['radar']}),'unexpected_phoenix_flags':sorted(f'{a}->{b}' for a,b in flagged-{(s['predecessor_id'],s['successor_id']) for s in manifest['phoenix']}),'note':'Scenario fixtures are constructed demonstrations; agreement here is not evidence of real-world detection performance.'}

def evaluate(data,outputs,elapsed):
    path=settings.data_dir/'evaluation/ground_truth.csv'
    if not path.exists():return {'status':'UNAVAILABLE','reason':'Separate ground-truth file not found.'}
    with path.open(newline='') as f:truth={r['claim_id']:r for r in csv.DictReader(f)}
    cutoff=min(c['service_date'] for c in data['claims'])+timedelta(days=455)
    eligible={c['claim_id'] for c in data['claims'] if c['service_date']>cutoff and c['claim_id'] in truth}
    positive={cid for cid in eligible if truth[cid]['injected_suspicious_pattern']=='1'}
    baselines={}
    for name,channels in [('rules_only',{'rules'}),('rules_plus_ml',{'rules','ml'}),('rules_ml_graph_supplytrace',{'rules','ml','graph','supplytrace'})]:
        scores={}
        for finding,_ in outputs:
            if finding.engine not in channels:continue
            for cid in set(finding.related_claim_ids)&eligible:scores[cid]=scores.get(cid,0)+({'HIGH':3,'MEDIUM':2,'LOW':1}[finding.severity]*finding.data_completeness)
        selected=sorted(scores,key=lambda cid:(-scores[cid],cid))[:100];tp=len(set(selected)&positive)
        baselines[name]={'k':len(selected),'precision_at_k':tp/len(selected) if selected else None,'recall_at_k':tp/len(positive) if positive else None,'false_positive_workload':len(selected)-tp,'synthetic_exposure_proxy_surfaced':round(sum(float(truth[cid]['synthetic_exposure_proxy_usd']) for cid in selected),2),'flagged_claims':len(scores)}
    with engine.connect() as con:
        ranked=[dict(r) for r in con.execute(select(cases).order_by(cases.c.priority_score.desc(),cases.c.case_id)).mappings()]
        links={}
        for r in con.execute(select(case_findings.c.case_id,finding_claims.c.claim_id).join(finding_claims,case_findings.c.finding_id==finding_claims.c.finding_id)).mappings():links.setdefault(r['case_id'],set()).add(r['claim_id'])
    eligible_cases=[c for c in ranked if links.get(c['case_id'],set())&eligible]
    selected=eligible_cases[:10]; surfaced=set().union(*(links[c['case_id']]&eligible for c in selected)) if selected else set()
    hit_cases=sum(bool(links[c['case_id']]&positive) for c in selected)
    case_metrics={'capacity':10,'selected_cases':len(selected),'precision_at_k':hit_cases/len(selected) if selected else None,'claim_recall_at_k':len(surfaced&positive)/len(positive) if positive else None,'false_positive_workload':len(selected)-hit_cases,'synthetic_exposure_proxy_surfaced':round(sum(float(truth[cid]['synthetic_exposure_proxy_usd']) for cid in surfaced),2)}
    report={'status':'CALCULATED','heldout_start_exclusive':str(cutoff),'heldout_claims':len(eligible),'injected_suspicious_claims':len(positive),'baselines':baselines,'capacity_ranking':case_metrics,'case_consolidation_rate':1-len(ranked)/max(len(outputs),1),'evidence_coverage':sum(bool(ev) for _,ev in outputs)/max(len(outputs),1),'processing_seconds':round(elapsed,2),'citation_validity':None,'unsupported_claim_rate':None,'notes':['Ground truth is used only in this aggregate offline evaluation.','Deterministic thresholds are fixed without fitting labels; anomaly training ends before the held-out period.','Supply peers use the analysis snapshot; this is retrospective screening, not a prospective replay.','Injected scenarios may be easy to detect; results do not estimate real-world fraud performance.','Claim baselines use K=100 claims; final capacity ranking uses K=10 cases. Their precision values are not directly comparable.','Citation and unsupported-claim metrics are populated separately by controlled tests.']}
    settings.artifact_dir.joinpath('evaluation').mkdir(parents=True,exist_ok=True)
    settings.artifact_dir.joinpath('evaluation/evaluation_report.json').write_text(json.dumps(clean(report),indent=2))
    return report

def analyze(progress=lambda v:None,train=True):
    start=time.monotonic();batch_id='AN-'+uuid4().hex[:16]
    with engine.begin() as con:
        con.execute(batches.insert().values(batch_id=batch_id,kind='analysis',status='RUNNING',source='Imported PostgreSQL snapshot',progress=0))
        data=load_data(con)
    if not data['claims']:raise ValueError('Import a valid dataset before analysis.')
    try:
        outputs=rules(data);progress(20)
        supplies,comparisons=supply_analysis(data);outputs+=supplies;progress(35)
        # Store computed comparisons once, serving bounded claim-specific portions.
        settings.artifact_dir.mkdir(parents=True,exist_ok=True)
        settings.artifact_dir.joinpath('supply_comparisons.json').write_text(json.dumps(comparisons))
        for name,fn in [('isolation_forest',train_anomaly),('forecasts',forecast_training)]:
            try:
                result=fn(data)
                if name=='isolation_forest':outputs+=result
            except Exception as exc:
                log.exception('Model stage failed: %s',name)
                con_metadata={'reason':type(exc).__name__,'detail':'Training failed; inspect local worker logs. Core rules remain available.'}
                with engine.begin() as con:con.execute(insert(models).values(model_version=f'FAILED-{name}-{batch_id}',model_type=name,status='FAILED',metadata=con_metadata))
        progress(65)
        graph_findings,graph_summary=graph_analysis(data,outputs);outputs+=graph_findings
        radar_findings,member_rows,batch_rows,batch_member_rows=radar.analyze(data);outputs+=radar_findings
        phoenix_findings,link_rows=phoenix.analyze(data);outputs+=phoenix_findings;progress(75)
        # Idempotent natural finding/evidence IDs preserve previous reviewer resolutions.
        outputs=list({f.finding_id:(f,ev) for f,ev in outputs}.values())
        with engine.begin() as con:
            con.exec_driver_sql('SELECT pg_advisory_xact_lock(8231702)')
            for f,ev in outputs:
                row=f.model_dump();row['score']=row.pop('anomaly_score_or_rule_result');row['created_at']=row.pop('detected_at')
                for k in ['related_claim_ids','related_provider_ids','related_facility_ids','evidence_ids']:row.pop(k)
                row['batch_id']=batch_id
                con.execute(insert(findings).values(**row).on_conflict_do_nothing())
                for cid in f.related_claim_ids:con.execute(insert(finding_claims).values(finding_id=f.finding_id,claim_id=cid).on_conflict_do_nothing())
                for e in ev:con.execute(insert(evidence).values(**e.model_dump()).on_conflict_do_nothing())
            persist_detector_tables(con,batch_id,member_rows,batch_rows,batch_member_rows,link_rows)
            case_count=consolidate(con,outputs,data)
        progress(90)
        evaluation=evaluate(data,outputs,time.monotonic()-start)
        evaluation['scenarios']=scenario_evaluation(batch_rows,link_rows)
        settings.artifact_dir.joinpath('evaluation/scenario_report.json').write_text(json.dumps(clean(evaluation['scenarios']),indent=2))
        report={'claims_processed':len(data['claims']),'findings':len(outputs),'cases':case_count,'graph':graph_summary,'member_radar':{'candidates':len(batch_rows),'qualified_batches':sum(b['qualified'] for b in batch_rows),'member_findings':sum(f.finding_type=='member_id_possibly_compromised' for f,_ in radar_findings),'profiles_available':bool(data.get('member_profiles'))},'phoenix':{'evaluated_pairs':len(link_rows),'flagged':sum(l['flagged'] for l in link_rows),'profiles_available':bool(data.get('provider_profiles'))},'evaluation':evaluation,'elapsed_seconds':round(time.monotonic()-start,2)}
        with engine.begin() as con:con.execute(update(batches).where(batches.c.batch_id==batch_id).values(status='COMPLETED',progress=100,report=clean(report),completed_at=now()))
        return {'batch_id':batch_id,**report}
    except Exception as exc:
        with engine.begin() as con:con.execute(update(batches).where(batches.c.batch_id==batch_id).values(status='FAILED',report={'error':type(exc).__name__},completed_at=now()))
        raise

