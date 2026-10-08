$ErrorActionPreference = 'Stop'
docker compose up -d db
$existing = docker compose exec -T db psql -U claimshield -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='claimshield_test'"
if (-not $existing.Trim()) { docker compose exec -T db createdb -U claimshield claimshield_test }
docker compose run --rm -e DB_NAME=claimshield_test -e ARTIFACT_DIR=/artifacts/test-run backend sh -c 'alembic upgrade head && pytest -q --junitxml=/artifacts/test-run/pytest-results.xml'
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
docker compose exec -T frontend npm test
if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed' }
