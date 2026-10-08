# CLAIMSHIELD NEXUS — PROJECT STATUS AUDIT

Audit date: 2026-10-08 · Performed per `PROJECT_HANDOFF.md` on the second laptop.
Everything marked COMPLETE below was executed in this session; historical claims were re-measured, not copied.

## A. Repository

- Path: `C:\Jeffrey\Projects\ClaimShield-Nexus\ClaimShield-Nexus` (nested one level below the opened workspace folder)
- Branch `main`, tracking `origin/main` (github.com/rayyandeys/ClaimShield-Nexus), latest commit `aa00f04 project handoff`, 3 commits, clean tree at start
- ~1,900 lines of dense source: backend 15 modules, 5 test files; frontend 4 page modules + components; 1 Alembic migration
- Missing expected assets:
  - `docs/demo_script.md` (linked from README) — absent
  - `artifacts/evaluation/verification_summary.md` (referenced by `docs/testing.md`) — absent
  - `artifacts/models/` — absent from git by design (`.gitignore`); regenerated this session
  - `README.md` ends with a stray UTF-16 line (`# ClaimShield-Nexus` appended by a PowerShell `echo >>`)

## B. Environment (this laptop)

| Item | Result |
|---|---|
| Docker | Desktop 29.6.2 (per-user install, was not running; started). Compose v5.3.1. WSL2 OK. 16 CPU / 7.6 GB to Docker |
| Build | First build ~22 min (pip download speed). `backend` and `worker` build the same image twice |
| PostgreSQL 17 | Fresh volume; migration `0001` applied automatically; healthy on 127.0.0.1:55432 |
| API | Healthy, http://localhost:8000, `/docs` 200 |
| Worker | Healthy (heartbeat) |
| Frontend | Healthy, http://localhost:5173 |
| `.env` | Created from `.env.example` (key left blank) |
| Groq | **A 56-char `GROQ_API_KEY` exists as a Windows user-scope environment variable. Compose interpolation prefers it over `.env`, so containers are configured with it.** |
| Model artifacts | Absent after clone → retrained with `python -m app.cli all`; 4 artifacts written and load/infer correctly |

## C. Data

| Table | CSV rows | Imported |
|---|---:|---:|
| claims | 20,000 | 20,000 |
| providers | 250 | 250 |
| facilities | 60 | 60 |
| claim_lines | 27,221 | 27,221 |
| supply_items | 7,081 | 7,081 |
| encounters | 19,705 | 19,705 |
| members / referrals / relationships / estimates / history / policies | 4,000 / 2,000 / 410 / 1,850 / 150 / 10 | all imported |

- Audit status `PASS_WITH_WARNINGS`. Confirmed: 130 empty `encounter_id`, 0 `correction_of_claim_id`, service dates 2024-10-01 → 2026-09-30.
- Ground truth (`data/evaluation/ground_truth.csv`, 1,400 injected claims across 9 scenarios) is never imported; claims API responses contain no label columns (checked).

## D. Feature matrix

| Feature | Status | Verification | Remaining |
|---|---|---|---|
| Docker / migrations / ingestion | COMPLETE | Fresh build, migrate, import; repeat import inserts 0 (integration test) | — |
| Claim rules | COMPLETE (partial coverage) | 931 rule findings: timing 284, duplicate 149, unbundling 140, upcoding 138, missing encounter 130, early refill 90 | `excessive_utilization` fires 0 times on real data |
| SupplyTrace | COMPLETE (partial coverage) | 487 quantity + 66 estimate-drift findings; browser view of C0009835 shows peer medians | `supply_unit_price_anomaly` and `duplicate_supply_charge` fire 0 times on real data |
| Isolation Forest | COMPLETE | Trained, artifact reloads, 250 scores, 26 provider findings, shown as percentile "not a probability" | — |
| 30/60/90 forecasts | COMPLETE, WEAK | Trained, reload + inference OK, UI shows values and PR-AUC | Deployed HGB is beaten by its own logistic benchmark at every horizon (see E) |
| Nexus graph backend | PARTIAL | Graph builds (24,328 nodes / 62,410 edges); bounded neighborhoods correct | **Graph engine produced 0 findings**: no provider has ≥8 referrals with ≥60% to one destination. The 250-claim injected referral scenario is undetected |
| Network visualization | COMPLETE | Browser: Cytoscape renders P0141, node click populates inspector | API ~1.3 s per request (reloads whole DB each call) |
| Evidence fusion / consolidation | COMPLETE | 1,510 findings → 894 cases; rerun stable (integration test) | — |
| Smart SIU queue | COMPLETE | Browser: ranked list, capacity control, row opens case | — |
| Dashboard / Claims / Forecast / Evaluation pages | COMPLETE | Browser + Vitest; counts come from backend | — |
| Case workspace | COMPLETE after fix | Browser | — |
| Investigation brief (UI) | **was BROKEN, fixed** | Frontend sent GET to POST-only `/brief`, `/challenge`, `/investigate` → "Method Not Allowed". Fixed in `frontend/src/pages/Cases.tsx`; brief now renders | — |
| Groq live generation | **BROKEN for large cases** | One live call (see F) returned **HTTP 413 Payload Too Large** for CASE-57780afce8a6f1931ba5; app fell back correctly | Shrink context (fewer findings/evidence, trimmed `observed_value`) |
| Groq fallback / validation | COMPLETE | Unit + controlled integration tests (valid, malformed, invented ID, rate limit, timeout) | Fallback is cached under the same key as a Groq success, so one transient failure pins the fallback until evidence version changes (`briefs.py` `compose`) |
| Evidence Challenger | IMPLEMENTED / UNVERIFIED in browser | Integration test passes (deterministic). Not clicked in the browser because it would call Groq without approval | Live check after 413 fix |
| Human review / recalculation / audit | COMPLETE | Browser on CASE-47a8e297e3771356602a: finding → EXPLAINED, evidence_version 1→2, audit event appended; integration test checks immutability trigger | — |
| Documentation | PARTIAL | README accurate on commands; graph contribution overstated | Add demo script, fix README tail |

## E. Machine learning (re-measured, identical to historical values; retraining is deterministic)

| Model | Artifact | Test prevalence | HGB PR-AUC | Logistic PR-AUC | HGB ROC-AUC | Logistic ROC-AUC |
|---|---|---:|---:|---:|---:|---:|
| 30-day | HGB30-f13c9fc96c62ef84e04f | 33/739 = 4.5% | 0.070 | **0.133** | 0.638 | **0.741** |
| 60-day | HGB60-f13c9fc96c62ef84e04f | 58/739 = 7.8% | 0.145 | **0.219** | 0.669 | **0.758** |
| 90-day | HGB90-f13c9fc96c62ef84e04f | 88/739 = 11.9% | 0.214 | **0.296** | 0.664 | **0.728** |
| Isolation Forest | IF-933500c5a615211254ad | — | — | — | — | — |

The 30-day HGB is barely above chance (PR-AUC 0.070 vs 0.045 prevalence; Precision@25 = 0.04, below prevalence). The simpler benchmark is better on every metric shown, so the wrong model is deployed. Detection evaluation (held-out 7,394 claims, 602 injected): Precision@100 rules 0.39, +ML 0.59, +ML+graph+SupplyTrace 0.92. Because the graph engine emits nothing, that last gain is SupplyTrace, not graph.

## F. Tests (this session)

- Backend: **54 passed, 0 failed** (`scripts/test.ps1`, isolated `claimshield_test` DB, 75.6 s)
- Frontend: **6 passed, 0 failed** (Vitest), re-run after the Cases.tsx fix; `tsc -b && vite build` passes in the image build
- `npm audit` (in container): 0 vulnerabilities
- Live API smoke (`python -m app.smoke`): PASS
- Browser walkthrough (Claude in Chrome): dashboard, SupplyTrace, network + selection, forecast, queue, case, brief, finding resolution — done
- Groq live: **one unintended live request** was sent when "Generate brief" was clicked (the user-scope key was not anticipated). Groq returned 413; no successful generation. No further live calls were made.
- Not executed: Evidence Challenger in browser, `groq-check`, Ingestion upload page

Fix applied to make the suite run on a fresh machine: `scripts/test.ps1` called `.Trim()` on `$null` when `claimshield_test` did not exist yet.

## G. Changes made during the audit

1. `scripts/test.ps1` — null-safe test-DB existence check.
2. `frontend/src/pages/Cases.tsx` — brief/challenge/investigate now POST (`api(path, {})`).
3. Regenerated tracked artifacts under `artifacts/evaluation/` and `artifacts/test-run/` (only timings/batch IDs differ) and new `artifacts/evaluation/live_smoke.json`.
4. Demo DB state: one cached deterministic brief for CASE-57780afce8a6f1931ba5 (evidence v1) and one resolution on CASE-47a8e297e3771356602a (append-only audit).

## H. Demo cases (stable content-hash IDs; identical on any machine with this dataset)

- **CASE-57780afce8a6f1931ba5** — rank 1, P0124, rules + ML + SupplyTrace, $36,251 review exposure. Use for brief / challenger.
- **CASE-e8c6414519c0a22203c6** — rank 12, P0141, 8 findings incl. estimate drift. Claim **C0009835** shows SupplyTrace (syringes 26 vs peer threshold 14) and estimate drift.
- Network: `P0141`. Forecast: `P0124` (1.7% / 10.2% / 15.7%).
- Note: resolving one finding on a claim still flagged by other findings will not change priority (correct behavior). To show a visible recalculation, resolve findings so an engine channel or a claim drops out.

## H2. Groq follow-up (same day, live testing approved by the user; key from the Windows user-scope env var)

Changes in `backend/app/services/briefs.py`:
- `compact_context`: top 8 findings / ≤3 evidence each, bookkeeping fields stripped, 12k-char budget (top case 35k → 10k chars). Fixes HTTP 413.
- Fallbacks caused by Groq failures are no longer cached; only successful Groq results (or offline-by-design reports) are cached.
- One retry on Groq `json_validate_failed`; `reasoning_effort: low`; `max_completion_tokens` 3000.
- Per-mode prompts (brief / challenge / investigate) with explicit `Possible: ` format.
- Verifier: hedge variants normalized; narrative numbers allowed only if present in the case evidence; currency and guilt language still disallowed. Failing facts/alternatives/actions are **dropped and counted in the warning** instead of discarding the whole report; a report with zero verified facts still falls back.
- Rejection reasons (locally authored) and Groq error codes are logged and shown in the warning.
- `scripts/test.ps1` runs tests with `GROQ_API_KEY=` so the suite never calls Groq.

Results: backend **64 passed**, frontend **7 passed** (new: compact-context budget, retry-after-failure, hedge normalization, numeric-provenance, partial-drop, brief-uses-POST).
Live: groq-check OK (`openai/gpt-oss-120b` available). Verified Groq output for CASE-57780afce8a6f1931ba5 brief / challenge / investigate and CASE-e8c6414519c0a22203c6 brief / challenge. Occasional 429 rate limits on back-to-back calls are absorbed by SDK retries (~30–50 s). Evidence Challenger rendered in the browser as AI-ASSISTED / SOURCE_VALUES_VERIFIED.

## I. Remaining work

**P0**
1. Fix Groq 413: cap context to ~5 findings / 20 evidence and strip `observed_value` to key fields in `briefs.py`. Proof: one approved live brief on CASE-57780afce8a6f1931ba5 returns `mode: groq`. (Estimate: 1–2 h.)
2. Stop caching fallback results under the success key (`compose` in `briefs.py`). Proof: unit test where a timeout followed by success yields `mode: groq`. (Estimate: 30 min.)
3. Add a Vitest that asserts brief/challenge use POST. (Estimate: 15 min.)

**P1**
4. Deploy the better forecast model (use the logistic benchmark, or select on validation PR-AUC) and report honestly. (2 h.)
5. Graph engine: make the referral rule data-aware (e.g. relative concentration vs specialty peers) so the injected network scenario is detectable, or state plainly that graph is context-only and relabel the evaluation row. (2–4 h.)
6. Write `docs/demo_script.md`; fix README tail; decide whether `.env`'s blank key should override a user-level env var (document it). (1 h.)
7. Investigate zero-firing rules (`excessive_utilization`, unit-price, duplicate supply). (1–2 h.)

**P2**: cache the loaded graph per analysis batch (network ~1.3 s); share one image between backend and worker; animations/extra charts.

## J. Demo readiness

Dashboard ✔ · real findings ✔ · open SIU case ✔ · resolve finding ✔ · recalculation ✔ (logic correct) · audit trail ✔ · Groq ✘ (413 → deterministic fallback, clearly labeled) · brief button ✔ after fix.
Demo-blocking issue remaining: none for an offline demo; live Groq requires P0 item 1.

## K. Acceptance checklist

[x] Docker services run · [x] migrations · [x] datasets imported · [x] relationships valid · [x] API serves real records · [x] React shows real data · [x] rules traceable · [x] SupplyTrace · [x] Isolation Forest inference · [~] Nexus graph uses real relationships but emits no findings · [x] 30/60/90 forecasts work (weak, see E) · [x] consolidation · [x] SIU ranking · [x] workspace evidence · [x] brief clearly indicates fallback mode · [x] LLM evidence references validated (tests) · [ ] Evidence Challenger verified in browser · [x] review decisions persist · [x] recalculation · [x] audit history · [x] automated tests pass · [x] browser workflow checked · [ ] reproducible demo documented · [x] no keys committed · [x] restorable on another computer (with the test.ps1 fix)
