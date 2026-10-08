import copy
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from app.models import SPECS

@pytest.fixture
def dataset():
    d={k:[] for k in SPECS}
    c={'claim_id':'C1','member_id':'M1','provider_id':'P1','facility_id':'F1','encounter_id':'E1','service_date':date(2025,1,1),'service_start':datetime(2025,1,1,9,tzinfo=timezone.utc),'service_duration_min':30,'claim_type':'professional','claim_status':'paid','submitted_date':date(2025,1,1),'payment_date':date(2025,1,2),'primary_code':'SIM-CONSULT-L1','correction_of_claim_id':None,'related_claim_id':None,'complexity_band':'low','billed_amount_usd':Decimal('100'),'allowed_amount_usd':Decimal('80'),'paid_amount_usd':Decimal('60'),'member_responsibility_usd':Decimal('20')}
    d['claims']=[c]
    d['encounters']=[dict(encounter_id='E1',member_id='M1',provider_id='P1',facility_id='F1',service_start=c['service_start'],duration_min=30,documented_code='SIM-CONSULT-L1',documented_complexity='low',encounter_source='test')]
    d['providers']=[dict(provider_id='P1',provider_name='Synthetic Test Provider',specialty='General',facility_id='F1')]
    d['facilities']=[dict(facility_id='F1',facility_name='Synthetic Facility',owner_id='O1')]
    d['members']=[dict(member_id='M1')]
    d['claim_lines']=[dict(claim_line_id='L1',claim_id='C1',procedure_code='SIM-CONSULT-L1',unit_count=1,unit_charge_usd=100,line_billed_usd=100,days_supply=None,days_since_last_fill=None)]
    for rule in ['duplicate_billing','missing_encounter_indicator','upcoding_indicator','impossible_timing','early_refill','unbundling_indicator','consumable_quantity_anomaly','estimate_to_final_drift']:
        d['billing_policies'].append(dict(policy_id=rule,rule_type=rule,applicable_claim_types='professional,facility,home_health,pharmacy',effective_from=date(2024,1,1),effective_to=date(2026,12,31),limitation='Synthetic test policy'))
    return d
