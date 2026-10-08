# Verification

`scripts/test.ps1` creates `claimshield_test` only if absent, applies Alembic migrations and runs the Python suite with isolated artifacts. It never drops databases or modifies the demo investigation database. Tests include source validation failures, identical imports, correction exceptions, timing, bundle policy behavior, supply outliers/context, graph sources, unique exposure, as-of features, model persistence, chronological boundaries, full review workflow, immutable audit, restart lease recovery and controlled Groq responses.

Integration tests train on the actual source CSVs in the isolated database. They test identical reruns and preservation of reviewer resolutions. Test artifacts are under `artifacts/test-run/`; source operational artifacts are under `artifacts/evaluation/`. A full integration run can take a few minutes on Docker Desktop.

Vitest exercises the React dashboard, real pagination request parameters, queue-to-case navigation, graph response rendering and empty/error states using controlled API fixtures. Fixtures are only in tests; production pages fetch the local API.

The read-only live smoke command exercises actual running REST routes, source-backed case evidence, graph and model responses. Browser inspection supplements component tests. Groq mocks cover success, malformed JSON, invented evidence, rate limits and timeouts. A live Groq call is not claimed without an available configured key.

Investigation extensions: `tests/test_features.py` (35 unit tests: catalog/taxonomy, Beta smoothing, scorer reuse, outcome policy, tie handling, capacity relevance, radar windows and signals, batch and look-alike detection, Phoenix components, eligibility window, identifier and address rules) and `tests/test_features_integration.py` (11 end-to-end tests on a disposable `claimshield_features_test` database recreated by `scripts/test.ps1`: scenario outcomes, unchanged original metrics, consolidation without predecessor findings, unique exposure, radar/Phoenix/graph APIs, recommendations from the real scorer, read-only simulation with table hashes and a database-level write rejection, evidence lifecycle and acquisition resolution, confirmation loop and three-denial rule, spillover leads, and reanalysis without duplicates or reversed reviews). Frontend `src/features.test.tsx` covers Member Radar, graph edge types, Phoenix components, Next-Best-Evidence, hypothetical labeling, confirmation requests, pending/error states, refresh after an outcome and the audit timeline.

Latest run (2026-10-08): backend 98 passed, feature workflow 11 passed, frontend 15 passed; live API smoke PASS on the augmented demo database. The browser walkthrough is recorded in [demo_script.md](demo_script.md).

Live claims monitoring: `tests/test_stream.py` has 16 unit tests, with no database:
- bundles pass the importer's row checks, references are consistent, IDs are unique and session-specific
- deterministic generation and the scenario mix, from 5 to 500 claims
- the duplicate, overlap and supply-outlier scenarios satisfy the existing rules; ordinary claims trigger none
- invalid configurations are rejected; insufficient care-context pools are rejected

`tests/test_stream_integration.py` has 15 end-to-end tests on `claimshield_features_test`, after the feature suite. Sessions are driven step by step with the same functions the runner and worker call. They cover:
- start: 503 without a runner, 422 for an invalid config, 409 for a duplicate start, a single active session
- insufficient history reported as INSUFFICIENT_DATA
- a full 60-claim session with genuine duplicate, supply, overlap and member-batch findings, cases and queue positions
- source-linked findings and the claim, SupplyTrace and network detail endpoints
- the Fully Analyzed invariant: no claim is complete with a non-terminal stage
- the radar batch for the cohort, reported separately from unexpected scenario flags
- SIU priority equal to `rank_values`, and unique exposure
- Next-Best-Evidence reading the updated case, with a read-only simulation
- preservation of every pre-existing finding status, audit event, case status and evidence request
- duplicate job delivery and re-delivered claims creating no duplicates
- ordered, resumable events: cursor paging and SSE `Last-Event-ID`
- validation rejection (including a dependent claim), lease protection, stale-snapshot rejection, screening failure → retry → FAILED → controlled retry, model-unavailable FAILED stages, stop during processing and draining, interrupted-run recovery
- replay with the same seed under new IDs
- cohort pool selection without overlap
- an unrecoverable session error failing visibly

`frontend/src/stream.test.tsx` has 9 tests:
- Start and Stop call the backend with the selected configuration
- the counters come from the server and are not conflated
- the feed shows API events, filters them and navigates to Claim detail and Case Workspace
- the inspector shows pending and failed stages, and *Fully analyzed* only when reported
- completion and errors are displayed
- provider events route to radar, network and forecast

A measured live run uses `docker compose exec backend python -m app.stream_measure` (see [LIVE_CLAIMS_MONITORING.md](LIVE_CLAIMS_MONITORING.md#9-performance-results-measured)).
