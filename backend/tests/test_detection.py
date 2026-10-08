import copy
from datetime import timedelta
import pytest
from app.services.detection import rules,supply_analysis

def types(outputs):return {f.finding_type for f,_ in outputs}

def test_genuine_duplicate(dataset):
    dataset['claims'].append(dict(dataset['claims'][0],claim_id='C2'))
    result=rules(dataset);assert 'duplicate_billing' in types(result)
    f,ev=next(pair for pair in result if pair[0].finding_type=='duplicate_billing')
    assert len(ev)==2 and len(f.related_claim_ids)==2

@pytest.mark.parametrize('status',['paid','corrected','replaced'])
def test_corrected_replacement_excluded(dataset,status):
    dataset['claims'].append(dict(dataset['claims'][0],claim_id='C2',correction_of_claim_id='C1',claim_status=status))
    assert 'duplicate_billing' not in types(rules(dataset))

def test_missing_encounter_is_gap(dataset):
    dataset['claims'][0]['encounter_id']=None
    f,_=next(pair for pair in rules(dataset) if pair[0].finding_type=='missing_encounter_indicator')
    assert f.data_completeness==.5 and 'not proof' in f.limitations[0]

def test_upcoding_requires_documented_conflict(dataset):
    dataset['claims'][0]['primary_code']='SIM-CONSULT-L3'
    assert 'upcoding_indicator' in types(rules(dataset))
    dataset['encounters'][0]['documented_complexity']='high'
    assert 'upcoding_indicator' not in types(rules(dataset))

def test_overlapping_schedules(dataset):
    dataset['claims'].append(dict(dataset['claims'][0],claim_id='C2',member_id='M2',encounter_id='E2'))
    dataset['encounters'].append(dict(dataset['encounters'][0],encounter_id='E2',member_id='M2',service_start=dataset['encounters'][0]['service_start']+timedelta(minutes=10)))
    assert 'impossible_timing' in types(rules(dataset))
    dataset['encounters'][1]['service_start']+=timedelta(hours=1)
    assert 'impossible_timing' not in types(rules(dataset))

def test_configured_bundle(dataset):
    dataset['claims'][0]['claim_type']='facility'
    dataset['claim_lines']=[dict(claim_line_id='L1',claim_id='C1',procedure_code='SIM-PACKAGE-BASE'),dict(claim_line_id='L2',claim_id='C1',procedure_code='SIM-PACKAGE-COMP')]
    assert 'unbundling_indicator' in types(rules(dataset))
    dataset['billing_policies']=[p for p in dataset['billing_policies'] if p['rule_type']!='unbundling_indicator']
    assert 'unbundling_indicator' not in types(rules(dataset))

def test_legitimate_high_complexity_utilization(dataset):
    original=dataset['claims'][0]
    dataset['claims']=[dict(original,claim_id=f'C{i}',complexity_band='high',service_date=original['service_date']+timedelta(days=i%25),service_start=original['service_start']+timedelta(days=i%25)) for i in range(25)]
    assert 'excessive_utilization' not in types(rules(dataset))

def supply_peers(dataset):
    c=dataset['claims'][0];c['claim_type']='facility'
    dataset['claims']=[dict(c,claim_id=f'C{i}') for i in range(25)]
    dataset['supply_items']=[dict(supply_id=f'S{i}',claim_id=f'C{i}',claim_line_id=f'L{i}',supply_code='GLOVE',supply_description='Synthetic gloves',quantity=10,unit_charge_usd=2,line_billed_usd=20) for i in range(25)]

@pytest.mark.parametrize('field,kind',[('quantity','consumable_quantity_anomaly'),('unit_charge_usd','supply_unit_price_anomaly')])
def test_supply_outliers(dataset,field,kind):
    supply_peers(dataset);dataset['supply_items'][-1][field]=100
    output,comparisons=supply_analysis(dataset)
    assert kind in types(output)
    assert comparisons['S24']['peer_count']==25

def test_complexity_matched_extra_supplies(dataset):
    supply_peers(dataset)
    for c in dataset['claims']:c['complexity_band']='high'
    for s in dataset['supply_items']:s['quantity']=100
    assert 'consumable_quantity_anomaly' not in types(supply_analysis(dataset)[0])

def test_missing_peer_group(dataset):
    supply_peers(dataset);dataset['supply_items']=dataset['supply_items'][:1]
    output,comparisons=supply_analysis(dataset)
    assert not output and comparisons['S0']['quantity']['available'] is False

def test_duplicate_supply_and_estimate(dataset):
    supply_peers(dataset);dataset['supply_items'].append(dict(dataset['supply_items'][0],supply_id='SDUP'))
    dataset['claim_estimates']=[dict(estimate_id='EST1',claim_id='C0',estimated_billed_usd=100,final_billed_usd=300,difference_usd=200)]
    kinds=types(supply_analysis(dataset)[0])
    assert {'duplicate_supply_charge','estimate_to_final_drift'}<=kinds
