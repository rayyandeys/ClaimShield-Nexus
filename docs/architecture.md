# Architecture and persistence

Four Docker services share a local bridge network. PostgreSQL 17 stores relational source tables, operational entities and a durable job queue. FastAPI exposes versioned REST routes. A separate worker loads one analysis snapshot, computes engines and saves source-linked findings/cases. React reads bounded API responses and never reads CSVs or model artifacts directly.

## Storage

`app/models.py` maps the inspected source headers to typed PostgreSQL columns, not whole-row JSON. Primary keys retain original IDs. Foreign keys are deferred to support legitimate forward claim references inside one import transaction. Financial columns use fixed-precision decimals. Entity registry rows permit typed provider/facility/member/claim/owner graph endpoints; owners are derived from explicit `facilities.owner_id` values.

JSONB is used for evidence observations, flexible model metadata, ranking contributions and audit state snapshots. Source-file checksum, import batch, original record ID and import timestamp provide provenance. Findings and evidence have content-derived stable IDs. A database trigger rejects updates and deletes on `audit_events`.

## Import boundary

The importer validates the complete snapshot before source insertion. Missing files, wrong headers, duplicate identifiers, invalid types/amounts/timestamps or broken references reject the import. Reports persist outside the source transaction. Warnings retain contextual conflicts for investigators. Imports take a PostgreSQL advisory transaction lock and compare existing source values before insertion. Identical snapshots reuse the original import and insert zero rows.

## Queue

Job claiming uses `SELECT FOR UPDATE SKIP LOCKED`. Running jobs have a renewable 90-second lease; a heartbeat renews it every ten seconds. A restarted worker can reclaim expired work. Retries are bounded at three attempts and failures are persisted. The UI/API limits submissions to one queued/running job, avoiding simultaneous training against shared artifacts. SIGTERM requests graceful completion; abrupt termination is recovered through lease expiry and idempotent writes.

## Analysis and case fusion

Rules and SupplyTrace emit the same Pydantic finding/evidence types as the anomaly and graph stages. Local models are isolated failure domains: a failed model card does not prevent core deterministic analysis. ML provides descriptive anomaly signals, forecasts provide behavioral probabilities, and ranking uses completeness as an evidence-strength proxy. None of these is an investigator determination.

The consolidation graph connects claims sharing a finding and claims from the same provider-month. Connected components form cases. Provider anomaly evidence is bounded to ten recent claims, and referral evidence is bounded. Case exposure deduplicates claim IDs, and the overview deduplicates across all active findings. Reviewer resolutions persist on the original finding and are not overwritten by identical analysis reruns.

## LLM boundary

Only local retrieval can access PostgreSQL. Groq receives a bounded evidence snapshot (at most twenty findings and eighty evidence records), with no API key or unrelated source population. Its strict response structure separates exact field/value facts from hypothetical alternatives. Local validation checks case membership, finding membership and exact values. The LLM has no tools capable of changing database state. Reports are cached by case, evidence version, capability, model and configured/offline mode.

## Limitations

The demo identity is not authentication. A localhost process can invoke review APIs. File artifacts are trusted local joblib files and must not be replaced with untrusted pickle content. Audit immutability is enforced for application operations, not against a PostgreSQL administrator. Snapshot analysis is designed for approximately twenty thousand claims; this is not a distributed analytics system.
