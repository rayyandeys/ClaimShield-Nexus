import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.services.briefs import Synthesis,Fact,Alternative,verify_synthesis,fallback

@pytest.fixture
def case():
    return {'case_id':'CASE1','findings':[{'finding_id':'F1','finding_type':'duplicate_billing','evidence_ids':['E1']}],'evidence':[{'evidence_id':'E1','finding_id':'F1','observed_value':{'billed_amount_usd':120.5,'claim_id':'C1'}}]}

def valid():return Synthesis(facts=[Fact(evidence_id='E1',field='billed_amount_usd',value='120.5')],alternatives=[Alternative(finding_id='F1',possible_explanation='Possible: a correction explains the repeated submission.',evidence_ids=['E1'],missing_information='Adjustment records.',suggested_action='Verify original records.',uncertainty='Clinical context is incomplete.')],next_actions=['Request adjustment records.'])

def test_valid_structured_response(case):assert verify_synthesis(valid(),case)

@pytest.mark.parametrize('field,value',[('evidence_id','INVENTED'),('value','999.0'),('field','invented_field')])
def test_invented_evidence_or_amount_rejected(case,field,value):
    result=valid();setattr(result.facts[0],field,value)
    with pytest.raises(ValueError):verify_synthesis(result,case)

def test_cross_case_reference_rejected(case):
    result=valid();result.alternatives[0].evidence_ids=['E_OTHER']
    with pytest.raises(ValueError):verify_synthesis(result,case)

def test_unsupported_narrative_rejected(case):
    result=valid();result.next_actions=['The provider owes $5000.']
    with pytest.raises(ValueError):verify_synthesis(result,case)

def test_malformed_response():
    with pytest.raises(ValidationError):Synthesis.model_validate_json('{broken')

def test_offline_counterevidence_is_hypothetical(case):
    result=fallback(case)
    assert result.alternatives[0].possible_explanation.startswith('Possible:')
    assert 'corrected or replaced' in result.alternatives[0].possible_explanation
