# Verification

`scripts/test.ps1` creates `claimshield_test` only if absent, applies Alembic migrations and runs the Python suite with isolated artifacts. It never drops databases or modifies the demo investigation database. Tests include source validation failures, identical imports, correction exceptions, timing, bundle policy behavior, supply outliers/context, graph sources, unique exposure, as-of features, model persistence, chronological boundaries, full review workflow, immutable audit, restart lease recovery and controlled Groq responses.

Integration tests train on the actual source CSVs in the isolated database. They test identical reruns and preservation of reviewer resolutions. Test artifacts are under `artifacts/test-run/`; source operational artifacts are under `artifacts/evaluation/`. A full integration run can take a few minutes on Docker Desktop.

Vitest exercises the React dashboard, real pagination request parameters, queue-to-case navigation, graph response rendering and empty/error states using controlled API fixtures. Fixtures are only in tests; production pages fetch the local API.

The read-only live smoke command exercises actual running REST routes, source-backed case evidence, graph and model responses. Browser inspection supplements component tests. Groq mocks cover success, malformed JSON, invented evidence, rate limits and timeouts. A live Groq call is not claimed without an available configured key.

Results and exact commands are recorded in `artifacts/evaluation/verification_summary.md` after execution.
