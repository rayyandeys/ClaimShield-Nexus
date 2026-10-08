# API and evidence contracts

Interactive route documentation: http://localhost:8000/docs. All collection routes use a predictable `items / total / page / page_size` envelope; the SIU queue additionally returns capacity and ranking-policy version. Claims pages are capped at 100 records. Graph responses are capped at 150 nodes and 300 edges. Provider lists are capped at 300.

The shared `Finding` and `Evidence` Pydantic contracts are in `backend/app/schemas.py`; corresponding TypeScript interfaces are in `frontend/src/types.ts`. Every engine retains original source IDs, provenance, version, limitations, completeness and status. Statistical anomaly magnitude, forecast probability, evidence completeness and human decision are distinct fields.

Routes under `/api/v1` cover dataset summary/audit, CSV snapshot upload, batch history, job submission/status, claims/details/SupplyTrace, providers/anomalies/forecast, network, cases/evidence/timeline, capacity queue, brief/challenge/investigate, finding resolution, reviewer actions and model/evaluation status. `/health` checks PostgreSQL connectivity.

`POST /analysis/run`: `{ "kind": "all" }` (also `import`, `analysis`, `train`). Returns HTTP 202 and a durable job. A currently queued/running job is returned instead of creating duplicate work.

`POST /cases/{id}/actions`: action type (`status`, `assign`, `note`, `request_records`), value and explanation, plus optional actor. Valid state transitions are returned with the case. Invalid transitions return 422. Demo actor defaults to Team CIPHER.

`POST /findings/{id}/resolve`: status (`EXPLAINED`, `UNRESOLVED`, `ESCALATED`), explanation of at least ten characters and one or more `verified_evidence_ids` from that finding. The action preserves the original finding, recalculates affected cases and appends audit events in one transaction.

Source records are read-only through operational APIs. Hidden ground-truth columns are never returned through claim, provider, case, graph or brief endpoints. Evaluation endpoints return aggregate metrics only. Missing records return 404; input validation returns 422; infrastructure failures remain errors and are not converted into fake empty records.
