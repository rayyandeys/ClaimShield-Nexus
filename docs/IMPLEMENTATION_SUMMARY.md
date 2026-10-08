# ClaimShield Nexus — Implementation Summary

Status as of **2026-10-08**. Covers everything done on this laptop since the project handoff: environment recovery, fixes to the existing system, the Groq integration, and three new investigation features. The three investigation features are committed; live claims monitoring is **not yet committed**.

Related documents:
- [PROJECT_STATUS_AUDIT.md](PROJECT_STATUS_AUDIT.md) — the original audit of the handed-over project
- [investigation_extensions.md](investigation_extensions.md) — algorithms, thresholds and limits of the three new features
- [demo_script.md](demo_script.md) — click-by-click demo with the real IDs and values
- [testing.md](testing.md) — test suites and how to run them

---

## 1. Environment recovery (second laptop)

| Item | Result |
|---|---|
| Docker Desktop | Per-user install; started; Compose v5.3.1 |
| Services | `db`, `backend`, `worker`, `frontend` all healthy |
| Database | Fresh volume; migrations applied; all 12 CSVs imported (20,000 claims, 250 providers, 60 facilities) |
| ML models | Absent from Git (ignored); retrained — results identical to the original laptop (1,510 findings, 894 cases) |
| `.env` | Created from `.env.example`; key not committed |
| Groq key | Read from a Windows **user-scope** environment variable, which Docker Compose prefers over `.env` |

## 2. Fixes to the existing system

| Fix | Files |
|---|---|
| "Generate brief", "Challenge evidence" and "Evidence investigator" buttons sent GET to POST-only routes ("Method Not Allowed") | `frontend/src/pages/Cases.tsx` |
| Test script crashed on a fresh machine (`.Trim()` on null) and broke under Windows PowerShell 5.1 quoting | `scripts/test.ps1` |
| Test runs no longer call Groq (blank key passed to test containers) | `scripts/test.ps1` |
| ML peer medians included providers that did not exist yet at the cutoff (temporal leakage; no effect on original data) | `backend/app/services/ml.py` |
| Network graph rebuilt on every request (1.7 s → 0.02 s with a data-version cache) | `backend/app/services/graph.py`, `backend/app/main.py` |
| Graphs could render as a tiny unfitted cluster (layout now runs after the canvas is measured) | `frontend/src/pages/Intelligence.tsx` |
| Garbled UTF-16 line at the end of the README | `README.md` |

## 3. Groq integration (now working live)

All changes in `backend/app/services/briefs.py`.

- **Request size:** Groq returned *413 Payload Too Large*. The context is now trimmed to the top findings and key evidence fields (35k → 10k characters).
- **Caching:** a failed Groq call was cached and never retried. Now only successful results are cached.
- **Prompts:** separate instructions for brief, challenger and investigator, with an explicit `Possible:` format; one retry on Groq `json_validate_failed`; low reasoning effort.
- **Verification:** items that fail checks (unverifiable facts, evidence from another finding, numbers not in the case evidence, currency, guilt language) are **dropped and counted** instead of discarding the whole report. A report with no verified fact still falls back to the deterministic version.
- **Diagnostics:** rejection reasons and Groq error codes are logged and shown in the warning.
- **Verified live:** brief, challenger and investigator returned source-verified AI output on the demo cases.

## 4. New features

### 4.1 Next-Best-Evidence
Recommends the three evidence requests most likely to change a capacity-limited review decision.

- **Catalog:** versioned, covers all 16 finding types (`backend/app/config/evidence_catalog.json`). Probabilities are labeled synthetic assumptions until reviewed outcomes exist (then Beta(1,1) smoothing).
- **Scoring:** every hypothetical uses the production SIU scorer (`rank_values`) through shared `case_inputs` / `apply_outcome` in `cases.py`, so simulations cannot drift from real ranking.
- **Simulation:** "what-if" previews run in PostgreSQL READ ONLY transactions and are labeled *HYPOTHETICAL — nothing was saved*.
- **Evidence requests:** lifecycle `REQUESTED → RECEIVED → VERIFIED_EXPLAINS | VERIFIED_SUPPORTS | INCONCLUSIVE | WITHDRAWN`; duplicates and repeat submissions are handled; reviewed outcomes add a source-linked evidence record, recalculate priority and write audit events.
- **UI:** Case Workspace tabs *Next evidence* and *Requests*; SIU queue card *Evidence that could change this queue*.

### 4.2 Compromised Member ID Radar + Member Confirmation Loop
- **Member signals:** first-contact burst, care-history mismatch, geographic jump, missing-encounter rate, cross-provider duplicates; robust median/MAD score with coverage.
- **Batch detection:** providers that suddenly bill many previously unrelated members (≥25 new, ≥5× growth, ≥80% unrelated, ≤20% prior care context; geography corroborating only).
- **Findings:** `stolen_id_batch` (provider) and `member_id_possibly_compromised` (member), with paginated member lists.
- **Confirmations:** deterministic, clearly *simulated* notices from the stored claim; Yes / No / Not sure responses only affect scoring after investigator review; three distinct reviewed denials raise the batch to HIGH.
- **Spillover:** other claims of batch members, grouped, labeled leads only and never added to exposure.
- **UI:** new *Member radar* page (batch list, threshold checks, acquisition chart, provider–member graph, member inspector, paginated tables); *Confirmations* and *Spillover* tabs in the Case Workspace.

### 4.3 Phoenix Provider Detector
- **Candidates:** suspended / revoked / closed providers paired with providers enrolled within 180 days afterwards (sorted index, no all-pairs comparison).
- **Score:** five transparent components — patient overlap, shared identifiers (house number must match for addresses), billing fingerprint, referral reuse, timing — combined into a *similarity score*, not a probability.
- **Findings:** `phoenix_successor` on the successor only; predecessor findings are never copied; documented acquisitions are surfaced as a possible legitimate explanation.
- **Graph:** dashed `possible_successor` edges in the existing Nexus network; clicking one shows the full breakdown.
- **UI:** breakdown panel in the network inspector and Case Workspace; successor banners on network, forecast, radar and case pages.

### 4.4 Shared integration
- **Pipeline:** Member Radar and Phoenix run inside the existing analysis job (`pipeline.py`), after rules, SupplyTrace, ML and graph.
- **Consolidation:** one new rule — provider-level findings about the same provider join one case. No existing case changed.
- **Audit:** request created / received / reviewed / withdrawn, finding corroborated, simulated notice generated, simulated response recorded (never "member contacted").
- **Evaluation:** scenario results written to `artifacts/evaluation/scenario_report.json` and shown on *Models & evaluation*.

### 4.5 Live Claims Monitoring (added 2026-10-08)
Overview gets a **Live Claims Monitor** with Start/Stop. A backend generator streams synthetic claims (default 60 at 1/s) through the real pipeline:
- **Immediate screening** in a new `stream` service: validation, persistence, Claim Rules, SupplyTrace, findings, case linking, SIU recalculation.
- **Background micro-batches** on the existing worker (`stream_enrichment` jobs every 15 s): Isolation Forest and forecast inference with the stored models, Nexus graph, Member Radar and Phoenix, all scoped to the affected providers and members, then final case reconciliation.
- **Coverage:** every claim is tracked across 12 stages and is *Fully analyzed* only when all are terminal.
- **Events:** committed database events (outbox) delivered by cursor polling (SSE also available). Each event is clickable to the claim, case, radar, network or forecast view.
- **Isolation:** each session uses its own synthetic provider cohort, so replays never touch P0252/P0258.

Details: [LIVE_CLAIMS_MONITORING.md](LIVE_CLAIMS_MONITORING.md) · walkthrough: [LIVE_STREAM_DEMO.md](LIVE_STREAM_DEMO.md).

**Measured (STREAM-0003):**
- 60/60 claims fully analyzed, 0 failed, at 1.02/s
- end-to-end latency mean 11.2 s, p95 18.0 s
- micro-batch 3.6 s mean
- the dashboard API stayed at about 48 ms median during the stream

**Migration 0003** (additive): `stream_sessions`, `stream_claims`, `stream_claim_stages`, `stream_events`, `stream_enrichment_runs`, `stream_runners`.

## 5. Database and data

**Migration `0002`** (additive only; safe on populated databases): `provider_profiles`, `member_profiles`, `evidence_requests`, `member_confirmations`, `member_risk`, `radar_batches`, `radar_batch_members`, `provider_successors`.

**Scenario pack v1** (`backend/app/scenarios.py`, `python -m app.cli augment`, seed 20261009): copies the 12 original CSVs unchanged and appends 14 providers, 1,151 claims, 881 encounters, 295 referrals, 15 relationships, 1 investigation record, plus profiles for all 264 providers and 4,000 members. Labels are kept in a separate manifest that detection never reads. All scenario activity is dated after the ML training window; on the original records, the 26 anomaly flags and the evaluation metrics (0.39 / 0.59 / 0.92) are unchanged.

| Scenario | IDs | Result |
|---|---|---|
| Suspicious member batch | P0252 | Flagged (270 new members, 8.6× growth) |
| Legitimate fast-growing practice | P0259 | Not flagged |
| Phoenix pair 1 (obvious) | P0251 → P0252 | Flagged, 0.82 |
| Phoenix pair 2 (no shared identifiers) | P0253 → P0254 | Flagged, 0.61 |
| Address-only look-alike | P0255 → P0256 | Not flagged, 0.06 |
| Documented practice acquisition | P0257 → P0258 | Flagged 0.59, resolvable with ownership records |

## 6. Tests (latest full `scripts/test.ps1` run, 2026-10-08)

| Suite | Result |
|---|---|
| Backend unit + existing integration (`claimshield_test`) | 114 passed (98 + 16 stream generator) |
| Feature + live-stream workflows end-to-end (disposable `claimshield_features_test`) | 26 passed (11 + 15 stream) |
| Frontend (Vitest) | 24 passed (15 + 9 Live Claims Monitor) |
| TypeScript / production build | Pass |
| Live API smoke on the demo database | PASS |
| Browser walkthrough (8 demo stages, real clicks) | Done (before live monitoring) |
| Measured live stream (STREAM-0003, demo stack) | 60/60 fully analyzed, 0 failed |

Run everything with `./scripts/test.ps1` from PowerShell.

## 7. Current state of the demo database

The demo database (`claimshield`) has the scenario pack applied and contains actions from the walkthrough:
- One resolved finding from the original audit (CASE-47a8e297e3771356602a).
- Three reviewed simulated denials on `CASE-27cd5d9b606a132d854e` (P0252); batch severity is HIGH.
- The P0258 Phoenix finding resolved via ownership records (`CASE-a2bd7630032dd71dfa4d`).

Stage 5 of the demo can be repeated with remaining claims; repeating stage 8 exactly needs a fresh database.

Live stream sessions on the demo database: STREAM-0001 (completed), STREAM-0002 (stopped, 0 claims) and STREAM-0003 (clean measured run). Together they add 120 claims, 14 findings and 6 cases for the cohort providers PS0001* and PS0003*. Content hashes taken before and after show that every pre-existing finding status, audit event, case, evidence request and confirmation is unchanged.

## 8. Known limitations

- Synthetic data and constructed scenarios; thresholds and weights chosen by hand.
- Next-Best-Evidence probabilities are synthetic assumptions (no evidence-specific outcome history yet).
- No diagnosis data; care history is approximated from prior same-service claims and referrals.
- Geography is synthetic and does not separate providers in this dataset, so it is displayed but not required.
- Member responses are simulated; no one is ever contacted.
- A successor link is a reason to investigate; a legitimate acquisition can look identical until documents are reviewed.
- Findings from an older model version can remain active after the source data changes (pre-existing limitation).
- Graph auto-fit was confirmed in tests and by reasoning; the automated browser ran in a hidden tab, so a visual check in a normal browser is still worthwhile.

## 9. Remaining suggestions

1. Commit the work (nothing is committed yet).
2. Switch the forecast model to the stronger logistic benchmark, or select by validation PR-AUC.
3. Make the original graph engine's referral rule detect the injected referral pattern, or relabel the evaluation row.
4. Decide whether `.env` or the Windows environment variable should own the Groq key, and document it.
