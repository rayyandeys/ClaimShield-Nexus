# ClaimShield Nexus — setup on a new machine (instructions for an AI coding agent)

You are setting up **ClaimShield Nexus** on a new Windows laptop so it runs exactly like the original: the same data, the same trained models, the same detections and the same UI. Follow the steps in order, run the verification commands, and report the results honestly. Do not skip verification.

---

## 0. Rules you must follow

- **Never** run `docker compose down -v`, `docker volume rm`, or anything else that deletes the PostgreSQL volume, unless the user explicitly asks.
- **Never** commit or print `.env`, API keys or secrets. Never push to GitHub or deploy unless asked.
- **Groq calls are billable.** Do not click *Generate brief*, *Challenge evidence* or *Evidence investigator*, and do not run live Groq checks, without the user's approval. The app works fully offline without a key.
- All data is synthetic. Do not modify `data/synthetic/*.csv`.
- **Rebuild all images.** After changing backend code, rebuild **all three** images built from `./backend`: `docker compose build backend worker stream`. Each compose service has its own image; rebuilding only `backend` leaves the worker running old code.
- **File encoding (Windows).** When editing files with Python, always use `open(path, encoding='utf-8')`. Python's default (cp1252) corrupts characters like `·`, `→` and `—`.

---

## 1. Prerequisites

| Requirement | Check |
|---|---|
| Windows 10/11 with WSL2 | `wsl --status` |
| Docker Desktop (Linux containers) running | `docker version` shows **both** Client and Server |
| Docker Compose v2 | `docker compose version` |
| Git | `git --version` |
| Free local ports 5173, 8000, 55432 | `netstat -ano \| findstr ":5173 :8000 :55432"` returns nothing |
| About 6 GB free disk, 8 GB RAM recommended | |

Host Python and Node are **not** required; everything runs in containers.

If `docker version` shows no Server section, start Docker Desktop and wait until it reports *Engine running*. A per-user install lives at `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`.

---

## 2. Get the code

```powershell
git clone https://github.com/rayyandeys/ClaimShield-Nexus.git
cd ClaimShield-Nexus
git log --oneline -3
```

Make sure the latest commit is present, and ask the user if it looks older than expected. In particular, the light UI theme, Live Claims Monitor and Compliance & Decisions tab must be in the history.

What the repository includes and excludes:
- **Included:** source code, the 12 synthetic CSVs in `data/synthetic/`, evaluation labels and the docs.
- **Excluded:** trained models (`artifacts/models/`), the scenario-pack snapshot (`artifacts/scenarios/`) and the database. Steps 4–5 rebuild them deterministically (fixed seeds), so results match the original exactly.

---

## 3. Configure

```powershell
Copy-Item .env.example .env
```

Defaults work as-is. Optional settings:

**Groq key.** Two ways to set it:
- Edit `.env`: `GROQ_API_KEY=...`
- Or set a Windows user environment variable named `GROQ_API_KEY`. Docker Compose prefers this over `.env`.

Never print or commit the key. After adding a key, run `docker compose up -d --force-recreate backend worker`.

**Stream defaults** (`STREAM_*` in `.env.example`) can stay as they are.

---

## 4. Build and start

```powershell
docker compose up --build -d
docker compose ps
```

Wait until all **five** services are `healthy`: `db`, `backend`, `worker`, `stream` and `frontend`. The first build takes several minutes.

- **Migrations:** the backend applies Alembic migrations automatically, up to `0004`.
- **Docker Hub timeouts:** if a build fails with `TLS handshake timeout` or another registry error, it is a network issue. Re-run the same build command; a few retries usually succeed.

---

## 5. Load data, train models, run analysis

Run these in order, waiting for each to finish:

```powershell
# 1. Import the 12 CSVs, train models, run the full analysis (a few minutes)
docker compose exec backend python -m app.cli all

# 2. Add scenario pack v1 (append-only synthetic scenarios: P0251–P0264)
docker compose exec backend python -m app.cli augment

# 3. Re-run analysis including the scenario pack
docker compose exec backend python -m app.cli analyze
```

Expected values after step 3 must match exactly:

| Measure | Expected |
|---|---|
| Claims | **21,151** |
| Findings | **1,599** |
| Cases | **898** |
| Scenario evaluation | 6/6 scenarios agree, no unexpected flags (*Models & evaluation* page) |
| Top of SIU queue | `CASE-27cd5d9b606a132d854e` (P0252), priority ≈ 86.7 |

Check them with:

```powershell
docker compose exec -T db psql -U claimshield -d claimshield -c "select (select count(*) from claims) claims, (select count(*) from findings) findings, (select count(*) from cases) cases"
```

---

## 6. Verify

```powershell
# Full automated test suite (backend unit, feature/policy/stream integration, frontend)
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1

# Read-only live API smoke test
docker compose exec backend python -m app.smoke
```

Expected results:
- `scripts/test.ps1` exits 0. It shows three result lines: backend (~122 passed), integration (~32 passed) and frontend (all files passed).
- The smoke test prints `"status": "PASS"`.

PowerShell 5.1 prints `NativeCommandError` lines for normal Docker stderr output; those are harmless if the exit code is 0.

`scripts/test.ps1` uses separate databases (`claimshield_test`, and a disposable `claimshield_features_test` it recreates). It never touches the demo database `claimshield`.

Then open **http://localhost:5173** and confirm:
1. **Dashboard:** light sidebar layout and stat cards. The **Live Claims Monitor** card shows only its header; click the arrow to expand it.
2. **SIU queue:** the ranked cases include a **Decision readiness** column.
3. **Case workspace:** opening `CASE-27cd5d9b606a132d854e` shows the tabs, including **Compliance & decisions**.
4. **Live stream:** *Start Live Simulation* (1 claim/s, 90 claims) streams claims, and *Fully analyzed* climbs to 90. This adds synthetic stream records to the database, which is expected and append-only.

Service addresses:

| Service | Address |
|---|---|
| UI | http://localhost:5173 |
| API | http://localhost:8000 (docs at `/docs`) |
| PostgreSQL | 127.0.0.1:55432, user `claimshield` |

---

## 7. Optional — copy the original laptop's exact database state

Steps 4–5 reproduce the **analyzed dataset** exactly. They do **not** include actions taken on the original laptop, which live only in its Docker volume:
- investigator reviews and confirmations
- resolved findings
- live-stream sessions
- policy decisions

To carry those over, ask the user to run this **on the original laptop**:

```powershell
docker compose exec -T db pg_dump -U claimshield -Fc claimshield > claimshield.dump
```

Then copy `claimshield.dump` and the folder `artifacts\models\` (the trained model files the stream inference uses) to the new laptop. Only with the user's explicit approval, because it replaces the new laptop's demo database, run:

```powershell
docker compose up -d db
docker compose exec -T db dropdb -U claimshield --if-exists claimshield
docker compose exec -T db createdb -U claimshield claimshield
Get-Content -Encoding Byte -ReadCount 0 .\claimshield.dump | docker compose exec -T db pg_restore -U claimshield -d claimshield --no-owner
docker compose up -d
```

On PowerShell 7 use `-AsByteStream` instead of `-Encoding Byte`. If piping binary data is unreliable, use `docker compose cp claimshield.dump db:/tmp/c.dump`, then `docker compose exec db pg_restore -U claimshield -d claimshield --no-owner /tmp/c.dump`.

Do not run step 5 after a restore; the data is already there.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `docker version` has no Server | Start Docker Desktop; enable WSL2 integration |
| Port already in use | Find the process using 5173/8000/55432 and stop it, or ask the user |
| Build fails with a registry or TLS error | Network; retry the build |
| UI shows empty data | Step 5 was not run; check `docker compose logs --tail 100 backend` |
| *Stream runner offline* on the dashboard | `docker compose up -d stream`; logs: `docker compose logs --tail 100 stream` |
| Stream claims never reach *Fully analyzed* | The worker runs enrichment. Check `docker compose ps worker` and rebuild with `docker compose build backend worker stream` |
| `curl`/`docker cp` paths mangled in Git Bash | Prefix the command with `MSYS_NO_PATHCONV=1` |
| Briefs show *DETERMINISTIC* | No Groq key, which is expected offline |

---

## 9. Where to read more

| Topic | Document |
|---|---|
| Feature overview and commands | `README.md` |
| Every screen and section | `docs/SCREEN_GUIDE.md` |
| Main investigation demo | `docs/demo_script.md` |
| Live stream demo | `docs/LIVE_STREAM_DEMO.md` |
| Live monitoring architecture and limits | `docs/LIVE_CLAIMS_MONITORING.md` |
| Policy and compliance decisions | `docs/POLICY_DECISIONS.md` |
| Test suites | `docs/testing.md` |
| What has been built | `docs/IMPLEMENTATION_SUMMARY.md` |

## 10. Report back

When finished, report:
- the `docker compose ps` result
- the claims, findings and cases counts from step 5
- the `scripts/test.ps1` result lines
- the smoke test status
- anything that differed from this document, and how you resolved it
