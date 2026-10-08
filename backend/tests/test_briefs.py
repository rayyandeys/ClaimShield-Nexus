import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.services.briefs import Synthesis,Fact,Alternative,verify_synthesis,fallback,compact_context,CONTEXT_CHAR_BUDGET

@pytest.fixture
def case():
    return {'case_id':'CASE1','findings':[{'finding_id':'F1','finding_type':'duplicate_billing','evidence_ids':['E1']}],'evidence':[{'evidence_id':'E1','finding_id':'F1','observed_value':{'billed_amount_usd':120.5,'claim_id':'C1'}}]}

def valid():return Synthesis(facts=[Fact(evidence_id='E1',field='billed_amount_usd',value='120.5')],alternatives=[Alternative(finding_id='F1',possible_explanation='Possible: a correction explains the repeated submission.',evidence_ids=['E1'],missing_information='Adjustment records.',suggested_action='Verify original records.',uncertainty='Clinical context is incomplete.')],next_actions=['Request adjustment records.'])

def test_valid_structured_response(case):assert verify_synthesis(valid(),case)

@pytest.mark.parametrize('field,value',[('evidence_id','INVENTED'),('value','999.0'),('field','invented_field')])
def test_invented_evidence_or_amount_rejected(case,field,value):
    result=valid();setattr(result.facts[0],field,value)
    with pytest.raises(ValueError):verify_synthesis(result,case)

def test_unverifiable_fact_dropped_verified_kept(case):
    result=valid();result.facts.append(Fact(evidence_id='E1',field='threshold',value='14'))
    assert [f.field for f in verify_synthesis(result,case).facts]==['billed_amount_usd']

def test_cross_case_reference_dropped(case):
    result=valid();result.alternatives[0].evidence_ids=['E_OTHER']
    assert verify_synthesis(result,case).alternatives==[]

def test_unsupported_narrative_dropped(case):
    result=valid();result.next_actions=['The provider owes $5000.','Request adjustment records.']
    assert verify_synthesis(result,case).next_actions==['Request adjustment records.']
    result=valid();result.alternatives[0].uncertainty='This is confirmed fraud.'
    assert verify_synthesis(result,case).alternatives==[]

@pytest.mark.parametrize('text',['possible - a correction explains it.','Possibly, a correction explains it.','Possible: a correction explains it.'])
def test_hedge_variants_normalized(case,text):
    result=valid();result.alternatives[0].possible_explanation=text
    assert verify_synthesis(result,case).alternatives[0].possible_explanation=='Possible: A correction explains it.'

@pytest.mark.parametrize('text',['A correction explains it.','Impossible to explain.','Possible:'])
def test_non_hypothetical_alternative_dropped(case,text):
    result=valid();result.alternatives[0].possible_explanation=text
    assert verify_synthesis(result,case).alternatives==[]

def test_narrative_numbers_must_come_from_evidence(case):
    result=valid();result.next_actions=['Confirm why the line shows 120.5 units of charge.','Confirm the 37 additional visits.']
    assert verify_synthesis(result,case).next_actions==['Confirm why the line shows 120.5 units of charge.']

def test_malformed_response():
    with pytest.raises(ValidationError):Synthesis.model_validate_json('{broken')

def test_compact_context_fits_budget_and_keeps_valid_references():
    findings=[{'finding_id':f'F{i}','finding_type':'consumable_quantity_anomaly','engine':'supplytrace','severity':'HIGH','explanation':'x'*200,'limitations':['l'*200]*3,'evidence_ids':[f'E{i}-{j}' for j in range(10)]} for i in range(20)]
    evidence=[{'evidence_id':f'E{i}-{j}','finding_id':f'F{i}','source_table_or_type':'supply_items','source_record_id':f'S{i}{j}','observed_value':{'quantity':26,'created_at':'2026-01-01','source_file':'supply_items.csv','note':'v'*300},'reference_value_or_context':{'threshold':14,'benign_explanations':['b'*300]}} for i in range(20) for j in range(10)]
    context=compact_context({'case_id':'CASE1','findings':findings,'evidence':evidence})
    assert len(json.dumps(context))<=CONTEXT_CHAR_BUDGET
    sent={e['evidence_id'] for e in context['evidence']}
    assert all(set(f['evidence_ids'])<=sent and f['evidence_ids'] for f in context['findings'])
    assert all('created_at' not in e['observed_value'] and 'benign_explanations' not in e['reference'] for e in context['evidence'])

def test_offline_counterevidence_is_hypothetical(case):
    result=fallback(case)
    assert result.alternatives[0].possible_explanation.startswith('Possible:')
    assert 'corrected or replaced' in result.alternatives[0].possible_explanation
