from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Evidence(Contract):
    evidence_id: str
    source_table_or_type: str
    source_record_id: str
    finding_id: str
    evidence_type: str
    observed_value: dict[str, Any]
    reference_value_or_context: dict[str, Any]
    record_timestamp: datetime | None = None
    provenance: dict[str, Any]
    verification_status: str = 'source_record'

class Finding(Contract):
    finding_id: str
    finding_type: str
    engine: str
    entity_type: str
    entity_id: str
    related_claim_ids: list[str]
    related_provider_ids: list[str]
    related_facility_ids: list[str]
    severity: Literal['LOW', 'MEDIUM', 'HIGH']
    anomaly_score_or_rule_result: float
    rule_or_model_version: str
    evidence_ids: list[str]
    explanation: str
    data_completeness: float
    limitations: list[str]
    detected_at: datetime
    status: str

class ActionInput(Contract):
    actor: str = Field(default='Demo Analyst · Team CIPHER', min_length=2, max_length=100)
    action_type: Literal['status', 'assign', 'note', 'request_records']
    value: str = Field(default='', max_length=100)
    explanation: str = Field(min_length=3, max_length=4000)

class ResolutionInput(Contract):
    actor: str = Field(default='Demo Analyst · Team CIPHER', min_length=2, max_length=100)
    status: Literal['EXPLAINED', 'UNRESOLVED', 'ESCALATED']
    explanation: str = Field(min_length=10, max_length=4000)
    verified_evidence_ids: list[str] = Field(min_length=1, max_length=30)

class JobRequest(Contract):
    kind: Literal['import', 'analysis', 'train', 'all'] = 'all'

class Page(Contract):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int

class ObjectResponse(BaseModel):
    model_config = ConfigDict(extra='allow')

