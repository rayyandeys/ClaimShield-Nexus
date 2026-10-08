$ErrorActionPreference = 'Stop'
docker compose up -d db
$existing = docker compose exec -T db psql -U claimshield -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='claimshield_test'"
if (-not "$existing".Trim()) { docker compose exec -T db createdb -U claimshield claimshield_test }
docker compose run --rm -e GROQ_API_KEY= -e DB_NAME=claimshield_test -e ARTIFACT_DIR=/artifacts/test-run backend sh -c 'alembic upgrade head && pytest -q --ignore=tests/test_features_integration.py --junitxml=/artifacts/test-run/pytest-results.xml'
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
# Feature workflow suite: claimshield_features_test is a disposable database owned by this script and recreated each run.
# It never touches the demo database (claimshield) or claimshield_test.
docker compose exec -T db dropdb -U claimshield --if-exists claimshield_features_test
docker compose exec -T db createdb -U claimshield claimshield_features_test
docker compose run --rm -e GROQ_API_KEY= -e DB_NAME=claimshield_features_test -e ARTIFACT_DIR=/artifacts/test-run-features backend sh -c 'alembic upgrade head && pytest -q tests/test_features_integration.py --junitxml=/artifacts/test-run-features/pytest-results.xml'
if ($LASTEXITCODE -ne 0) { throw 'Feature workflow tests failed' }
docker compose exec -T frontend npm test
if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed' }
