from datetime import timedelta
from copy import deepcopy
import numpy as np
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from app.services.graph import build_graph,bounded_network,graph_analysis
from app.services.cases import rank_values,TRANSITIONS
from app.services.ml import feature_frame,FEATURES,metrics

def test_graph_nodes_and_sources(dataset):
    dataset['relationships']=[dict(relationship_id='REL1',source_id='O1',target_id='F1',relationship_type='owns',effective_from=dataset['claims'][0]['service_date'],effective_to=None,verification_status='recorded')]
    graph=build_graph(dataset)
    assert set(graph.nodes)=={'P1','F1','M1','C1','O1'}
    assert graph['O1']['F1']['REL1']['source_record_id']=='REL1'
    assert bounded_network(dataset,'P1')['nodes']
    findings,summary=graph_analysis(dataset,[])
    assert findings==[] and summary['connected_components']>=1

def test_shared_ownership_context(dataset):
    dataset['facilities'].append(dict(facility_id='F2',facility_name='Second Synthetic Facility',owner_id='O1'))
    findings,summary=graph_analysis(dataset,[])
    assert summary['shared_ownership']['O1']==['F1','F2'] and not findings

def test_priority_no_double_count(dataset):
    fs=[dict(status='ACTIVE',claim_ids=['C1'],severity='HIGH',data_completeness=1,engine='rules'),dict(status='ACTIVE',claim_ids=['C1'],severity='HIGH',data_completeness=1,engine='supplytrace')]
    ranked=rank_values(fs,dataset['claims'],.3)
    assert ranked['potential_financial_exposure']==60
    assert ranked['ranking']['factors']['independent_channels']>0
    fs[0]['status']=fs[1]['status']='EXPLAINED'
    cleared=rank_values(fs,dataset['claims'],.3)
    assert cleared['priority_score']==0 and cleared['potential_financial_exposure']==0

def test_status_transition_contract():
    assert 'CLOSED' not in TRANSITIONS['UNDER_REVIEW']
    assert 'UNDER_REVIEW' in TRANSITIONS['NEW']

def test_asof_features_exclude_future_records(dataset):
    cutoff=dataset['claims'][0]['service_date']+timedelta(days=1)
    before=feature_frame(dataset,cutoff)
    later=deepcopy(dataset)
    later['claims'].append(dict(later['claims'][0],claim_id='FUTURE',service_date=cutoff+timedelta(days=5),submitted_date=cutoff+timedelta(days=5),allowed_amount_usd=999999))
    later['investigation_history'].append(dict(provider_id='P1',outcome='confirmed_improper_payment',outcome_available_date=cutoff+timedelta(days=1)))
    after=feature_frame(later,cutoff)
    np.testing.assert_array_equal(before[FEATURES].values,after[FEATURES].values)
    assert not any('scenario' in f or 'future' in f or 'injected' in f for f in FEATURES)

def test_unpaid_asof_cutoff(dataset):
    f=feature_frame(dataset,dataset['claims'][0]['service_date'])
    assert f.loc['P1','paid_total_peer_ratio']==0

def test_reproducible_model_and_reload(tmp_path):
    x=np.random.default_rng(42).normal(size=(100,4))
    a=Pipeline([('scale',StandardScaler()),('model',IsolationForest(random_state=42))]).fit(x)
    b=Pipeline([('scale',StandardScaler()),('model',IsolationForest(random_state=42))]).fit(x)
    np.testing.assert_array_equal(a.decision_function(x),b.decision_function(x))
    path=tmp_path/'model.joblib';joblib.dump(a,path)
    np.testing.assert_array_equal(a.predict(x),joblib.load(path).predict(x))

def test_metric_single_class_graceful():
    result=metrics([0,0,0],[.1,.2,.3]);assert result['roc_auc'] is None and result['pr_auc'] is None
