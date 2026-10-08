"""Local models with explicit as-of features and chronological, purged evaluation."""
import json
from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from scipy.stats import percentileofscore
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import IsolationForest, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss
from sqlalchemy.dialects.postgresql import insert
from app.core import settings, engine, clean
from app.models import models, predictions
from app.services.detection import make_finding, stable_id

SEED=20261008
FEATURES=['claim_count','allowed_mean_peer_ratio','allowed_total_peer_ratio','volume_change','member_count','procedure_count','observed_events','event_rate','paid_total_peer_ratio','referral_concentration','known_improper_outcomes']
TARGET='At least one new observable event for a provider with prior events, or at least two new events for a provider without prior events. Events: synthetic code mismatch, configured package conflict, early refill. Not criminal fraud.'

def event_claim_ids(data):
    encounters={r['encounter_id']:r for r in data['encounters']}; lines={}
    for l in data['claim_lines']: lines.setdefault(l['claim_id'],[]).append(l)
    events=set()
    for c in data['claims']:
        e=encounters.get(c['encounter_id']); ls=lines.get(c['claim_id'],[]); codes={l['procedure_code'] for l in ls}
        if (e and c['primary_code']=='SIM-CONSULT-L3' and e['documented_code']=='SIM-CONSULT-L1' and e['documented_complexity']=='low') or {'SIM-PACKAGE-BASE','SIM-PACKAGE-COMP'}<=codes or any(l.get('days_supply') and l.get('days_since_last_fill') is not None and l['days_since_last_fill']<.5*l['days_supply'] for l in ls): events.add(c['claim_id'])
    return events

def feature_frame(data, cutoff, events=None):
    events=events if events is not None else event_claim_ids(data)
    frame=pd.DataFrame(data['claims']); frame=frame[(frame.service_date<=cutoff)&(frame.submitted_date<=cutoff)].copy()
    recent=frame[frame.service_date>cutoff-timedelta(days=90)].copy()
    previous=frame[(frame.service_date<=cutoff-timedelta(days=90))&(frame.service_date>cutoff-timedelta(days=180))]
    recent['event']=recent.claim_id.isin(events).astype(int)
    recent['known_paid']=recent.apply(lambda r: float(r.paid_amount_usd) if r.payment_date and r.payment_date<=cutoff else 0.,axis=1)
    for col in ['allowed_amount_usd']: recent[col]=recent[col].astype(float)
    grouped=recent.groupby('provider_id').agg(claim_count=('claim_id','count'),allowed_mean=('allowed_amount_usd','mean'),allowed_total=('allowed_amount_usd','sum'),member_count=('member_id','nunique'),procedure_count=('primary_code','nunique'),observed_events=('event','sum'),paid_total=('known_paid','sum'))
    providers=pd.DataFrame(data['providers']).set_index('provider_id')
    f=providers[['specialty']].join(grouped).fillna(0)
    f['previous_count']=previous.groupby('provider_id').size().reindex(f.index).fillna(0)
    f['volume_change']=(f.claim_count-f.previous_count)/(f.previous_count+1)
    f['event_rate']=f.observed_events/f.claim_count.clip(lower=1)
    for col in ['allowed_mean','allowed_total','paid_total']:
        med=f.groupby('specialty')[col].transform('median').clip(lower=1)
        f[col+'_peer_ratio']=f[col]/med
    refs=pd.DataFrame([r for r in data['referrals'] if cutoff-timedelta(days=90)<r['referral_date']<=cutoff])
    f['referral_concentration']=0.
    if len(refs):
        counts=refs.groupby(['source_provider_id','destination_provider_id']).size()
        conc=counts.groupby(level=0).max()/counts.groupby(level=0).sum()
        f['referral_concentration']=conc.reindex(f.index).fillna(0)
    outcomes={}
    for r in data['investigation_history']:
        if r['outcome_available_date'] and r['outcome_available_date']<=cutoff and r['outcome']=='confirmed_improper_payment': outcomes[r['provider_id']]=outcomes.get(r['provider_id'],0)+1
    f['known_improper_outcomes']=pd.Series(outcomes,dtype=float).reindex(f.index).fillna(0)
    f['eligible']=f.claim_count>=3
    return f

def metrics(y,p,k=25):
    y=np.asarray(y);p=np.asarray(p);k=min(k,len(y));order=np.argsort(-p,kind='stable')[:k]
    bins=[]
    for low in np.arange(0,1,.2):
        mask=(p>=low)&(p<=low+.2 if low>.79 else p<low+.2)
        if mask.any():bins.append({'lower':round(float(low),2),'count':int(mask.sum()),'mean_prediction':float(p[mask].mean()),'observed_rate':float(y[mask].mean())})
    return {'n':len(y),'positives':int(y.sum()),'pr_auc':float(average_precision_score(y,p)) if y.sum() else None,'roc_auc':float(roc_auc_score(y,p)) if len(set(y))==2 else None,'brier':float(brier_score_loss(y,p)),'precision_at_k':float(y[order].mean()) if k else None,'recall_at_k':float(y[order].sum()/y.sum()) if y.sum() else None,'k':k,'calibration_bins':bins}

def save_model(version,typ,status,meta,artifact=None):
    with engine.begin() as con:
        statement=insert(models).values(model_version=version,model_type=typ,status=status,artifact_path=artifact,metadata=clean(meta))
        con.execute(statement.on_conflict_do_update(index_elements=['model_version'],set_={'status':status,'artifact_path':artifact,'metadata':clean(meta)}))

def save_predictions(rows):
    if not rows:return
    with engine.begin() as con:
        for r in rows:
            stmt=insert(predictions).values(**clean(r))
            con.execute(stmt.on_conflict_do_update(index_elements=['prediction_id'],set_={'value':r['value'],'details':clean(r['details'])}))

def train_anomaly(data):
    start=min(c['service_date'] for c in data['claims']);end=max(c['service_date'] for c in data['claims']);cutoff=min(start+timedelta(days=365),end-timedelta(days=90))
    version=stable_id('IF-',cutoff,end,SEED,len(data['claims']))
    train=feature_frame(data,cutoff); latest=feature_frame(data,end); train=train[train.eligible]
    if len(train)<30:
        save_model(version,'isolation_forest','INSUFFICIENT_DATA',{'reason':'At least 30 eligible historical provider snapshots required.'});return []
    pipeline=Pipeline([('scale',StandardScaler()),('model',IsolationForest(n_estimators=200,contamination=.05,random_state=SEED,n_jobs=2))])
    pipeline.fit(train[FEATURES]); raw=-pipeline.decision_function(latest[FEATURES]); train_raw=-pipeline.decision_function(train[FEATURES])
    path=settings.artifact_dir/'models'/f'{version}.joblib';path.parent.mkdir(parents=True,exist_ok=True)
    joblib.dump({'pipeline':pipeline,'features':FEATURES,'reference_scores':train_raw,'seed':SEED,'cutoff':str(cutoff)},path)
    meta={'features':FEATURES,'seed':SEED,'training_start':str(start),'training_end':str(cutoff),'scoring_cutoff':str(end),'training_providers':len(train),'normalization':'Percentile relative to historical training-provider anomaly scores; not a probability. Amount features normalized to specialty medians.','limitations':['Observable feature deviations are descriptive, not causal IsolationForest explanations.','Small synthetic provider population; no clinical validation.']}
    save_model(version,'isolation_forest','READY',meta,str(path))
    output=[];rows=[]
    for (pid,f),score in zip(latest.iterrows(),raw):
        percentile=float(percentileofscore(train_raw,score))/100
        signals={key:round(float(f[key]),4) for key in FEATURES}
        rows.append(dict(prediction_id=stable_id('P-',version,pid,0),provider_id=pid,model_version=version,horizon=0,cutoff=end,value=float(score),details={'anomaly_percentile':percentile,'signals':signals,'eligible':bool(f.eligible),'limitations':meta['limitations']}))
        if f.eligible and percentile>=.95:
            cs=sorted([c for c in data['claims'] if c['provider_id']==pid and c['service_date']>end-timedelta(days=90)],key=lambda c:float(c['allowed_amount_usd']),reverse=True)[:10]
            if cs:output.append(make_finding('provider_statistical_anomaly',cs,[('claims',c['claim_id'],c) for c in cs],f'Provider recent behavior is at historical anomaly percentile {percentile:.1%}.',score=float(score),engine='ml',entity=pid,context={'anomaly_percentile':percentile,'observable_signals':signals},version=version,limitations=meta['limitations']))
    save_predictions(rows);return output

def forecast_training(data):
    start=min(c['service_date'] for c in data['claims']);end=max(c['service_date'] for c in data['claims']);events=event_claim_ids(data)
    cutoff_dates=[];date=start+timedelta(days=90)
    while date<=end-timedelta(days=90):cutoff_dates.append(date);date+=timedelta(days=30)
    snapshots=[]
    event_dates={}
    for c in data['claims']:
        if c['claim_id'] in events:event_dates.setdefault(c['provider_id'],[]).append(max(c['service_date'],c['submitted_date']))
    for cutoff in cutoff_dates:
        f=feature_frame(data,cutoff,events);f=f[f.eligible].copy();f['cutoff']=cutoff;f['provider_id']=f.index
        for horizon in [30,60,90]:
            f[f'y{horizon}']=[int(sum(cutoff<d<=cutoff+timedelta(days=horizon) for d in event_dates.get(pid,[])) >= (1 if any(d<=cutoff for d in event_dates.get(pid,[])) else 2)) for pid in f.index]
        snapshots.append(f)
    dataset=pd.concat(snapshots,ignore_index=True) if snapshots else pd.DataFrame()
    latest=feature_frame(data,end,events);reports={}
    for horizon in [30,60,90]:
        version=stable_id(f'HGB{horizon}-',start,end,len(data['claims']),SEED)
        # Common 90-day embargo: all training outcomes finish before validation;
        # all validation outcomes finish before test. No random split.
        tr=dataset[dataset.cutoff<=start+timedelta(days=270)] if len(dataset) else dataset
        va=dataset[(dataset.cutoff>=start+timedelta(days=390))&(dataset.cutoff<=start+timedelta(days=450))] if len(dataset) else dataset
        te=dataset[dataset.cutoff>=start+timedelta(days=570)] if len(dataset) else dataset
        meta={'target':TARGET,'horizon':horizon,'features':FEATURES,'seed':SEED,'embargo_days':90,'splits':{name:{'rows':len(f),'start':str(f.cutoff.min()) if len(f) else None,'end':str(f.cutoff.max()) if len(f) else None,'positive':int(f[f'y{horizon}'].sum()) if len(f) else 0} for name,f in [('train',tr),('validation',va),('test',te)]},'limitations':['Synthetic event recurrence, not probability of fraud.','Providers can appear in multiple chronological periods; label windows across split boundaries do not overlap.','Within-split snapshots overlap; observations are correlated.','Probabilities are not post-hoc calibrated; inspect held-out calibration bins.','Horizons are independently fitted and need not be monotonic.']}
        if any(len(f)<50 or f[f'y{horizon}'].nunique()<2 or min(f[f'y{horizon}'].value_counts())<5 for f in [tr,va,te]):
            meta['reason']='Each chronological split requires at least 50 rows and five examples of both classes.';save_model(version,f'forecast_{horizon}','INSUFFICIENT_DATA',meta);reports[str(horizon)]=meta;continue
        hgb=HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=15,l2_regularization=5,random_state=SEED,early_stopping=False)
        benchmark=Pipeline([('scale',StandardScaler()),('model',LogisticRegression(max_iter=1000,random_state=SEED))])
        hgb.fit(tr[FEATURES],tr[f'y{horizon}']);benchmark.fit(tr[FEATURES],tr[f'y{horizon}'])
        meta['validation']=metrics(va[f'y{horizon}'],hgb.predict_proba(va[FEATURES])[:,1])
        meta['test']=metrics(te[f'y{horizon}'],hgb.predict_proba(te[FEATURES])[:,1])
        meta['logistic_benchmark_test']=metrics(te[f'y{horizon}'],benchmark.predict_proba(te[FEATURES])[:,1])
        path=settings.artifact_dir/'models'/f'{version}.joblib';path.parent.mkdir(parents=True,exist_ok=True)
        joblib.dump({'model':hgb,'benchmark':benchmark,'features':FEATURES,'metadata':meta},path)
        save_model(version,f'forecast_{horizon}','READY',meta,str(path));reports[str(horizon)]=meta
        values=hgb.predict_proba(latest[FEATURES])[:,1]
        save_predictions([dict(prediction_id=stable_id('P-',version,pid,horizon),provider_id=pid,model_version=version,horizon=horizon,cutoff=end,value=float(p) if f.eligible else None,details={'target':TARGET,'eligible':bool(f.eligible),'signals':{k:float(f[k]) for k in FEATURES},'limitations':meta['limitations']}) for (pid,f),p in zip(latest.iterrows(),values)])
    settings.artifact_dir.joinpath('evaluation').mkdir(parents=True,exist_ok=True)
    settings.artifact_dir.joinpath('evaluation/forecast_report.json').write_text(json.dumps(clean(reports),indent=2))
    return reports
