# Demo walkthrough — ClaimShield Nexus with investigation extensions

All values below were observed in a live browser walkthrough on 2026-10-08 against the local demo database after `cli augment` + `cli analyze`. IDs are content hashes or seeded IDs and are identical on any machine that runs the same setup. Every record is synthetic; every member response is simulated.

## Setup (fresh machine)

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose exec backend python -m app.cli all       # original snapshot, models, analysis
docker compose exec backend python -m app.cli augment   # scenario pack v1 (append-only)
docker compose exec backend python -m app.cli analyze
```

Open http://localhost:5173. Expected: 21,151 claims, 1,599 findings, 898 cases; *Models & evaluation → Scenario pack evaluation* shows 6/6 scenarios agree and no unexpected flags.

> The walkthrough below changes the demo database (notices, reviewed responses, one resolved Phoenix finding). Stage 5 can be repeated with any of the remaining eligible claims; stage 8 needs a fresh database to repeat exactly.

> Live streaming demo (optional, before stage 1): [LIVE_STREAM_DEMO.md](LIVE_STREAM_DEMO.md). Stream sessions add their own cohort records only; none of the IDs below change.

## Stage 1 — Member Radar

1. Sidebar → **Member radar** (after live-stream sessions, their cohort batches `PS000kC` sort first; type `P0252` in the search box). Row **P0252 · Synthetic Provider 252 · DME Supplier**: *Flagged*, 270 new members, 8.6× growth, 97% without prior relationship, 0% prior care context, dispersion percentile 43%.
2. Click the row (→ `/radar/P0252`). Threshold checks: four required checks *Passed*; geographic dispersion *Corroborating · Not met*, with the documented reason (care location in the base data is independent of residence). Historical rate 31.3 per 90 days (32 new members over 92 active baseline days). The acquisition chart spikes in Aug–Sep 2026.
3. Provider–member network: green = billed before the window, orange = new with no prior relationship. Click a member (e.g. M002165): the five signals with values, robust z and coverage (D missing-encounter rate 1.00, C geographic jump ≈ 2,675 km).
- **Conclusion:** a burst of previously unrelated members that needs member confirmation. **Uncertain:** whether any identity was actually misused; diagnoses are unavailable.

## Stage 2 — Phoenix

4. Banner on the batch page: *Possible successor relationship: P0251 (revoked) → P0252 · similarity 0.82* → **View graph edge**.
5. Nexus network for P0252 → *Relationship types* = **possible successor** → click the dashed edge (label 0.8185). Inspector: revoked 2026-03-01, successor began 23 days later; patient overlap 0.71, shared identifiers 0.90 (phone, bank token, practice owner), billing fingerprint 1.00, referral reuse 0.67, timing 0.87; 30 of 40 predecessor patients; shared referrers P0261, P0262; predecessor history INV00151 shown as *context only — not transferred*.
- **Conclusion:** strong reason to request ownership records. **Uncertain:** operational control is not established by similarity.

## Stage 3 — Consolidated case

6. **Open consolidated case** → `CASE-27cd5d9b606a132d854e`. One case holding the member batch, 82 member-level findings (collapsed group), the possible-successor finding and the ML anomaly; 310 claims, all P0252; review priority 86.7 (queue #1); potential review exposure **$86,400**, unique-claim upper bound, not confirmed loss.

## Stage 4 — Next-Best-Evidence

7. Tab **Next evidence**. #1 *Ownership / acquisition records* (synthetic default assumption, ~5 days, decision-change probability 40%, *Crosses capacity*): Explains (40%) → 71.2, #25, outside capacity; Supports (60%) → 86.7, #1.
8. **Simulate explains finding** → dashed panel *HYPOTHETICAL — nothing was saved*: 86.7 #1 → 71.2 #25; would enter the top 10: P0066; would leave: this case. The case header still shows 86.7.

## Stage 5 — Member confirmation

9. Tab **Confirmations** → **Generate simulated notice** for C0020158, C0020159, C0020160. Each notice cites the stored claim (e.g. "medical supply (catheter supplies) claim dated August 23, 2026 from Synthetic Provider 0252") and states that no message was sent.
10. Click **No** on each card → responses *NO*, requests *RECEIVED*; priority unchanged (responses have no effect until reviewed).

## Stage 6 — Spillover

11. Tab **Spillover**: 1,376 related claims across 267 members; billed $2,570,567 and paid $1,211,012 labeled *Leads only · not case exposure*. Case exposure stays $86,400.

## Stage 7 — Human decision

12. On each confirmation card choose **Verified · supports the concern**, enter notes, **Record reviewed outcome**. After the first: batch finding *ESCALATED*. After the third distinct member: batch severity **HIGH** (three-denial rule), 25 evidence records (22 detector + 3 reviews).
13. The computed priority stays **86.72 (#1)**: the case is already at the scoring ceiling for severity (30/30 from the high-severity Phoenix finding) and evidence completeness. The system does not inflate a score that has no headroom.
14. Tab **Timeline**: *evidence outcome reviewed* and *finding corroborated* events with the analyst's notes; *simulated member response recorded* and *simulated member notice generated* (never "member contacted").
15. Optional with a Groq key: **Generate brief** / **Challenge evidence** returned *AI-ASSISTED · SOURCE_VALUES_VERIFIED* (14 and 13 verified facts; unverifiable AI items discarded and counted).

## Stage 8 — False-positive safeguards

16. Member Radar: **P0259** (practice absorbing a closing local practice) is *Not flagged* — 60 new members and 6.5× growth, but only 8% lack a prior relationship (referrals) and 92% have prior care context.
17. *Models & evaluation*: the address-only pair **P0255 → P0256** is not flagged (similarity 0.06; only the address matches).
18. Case `CASE-a2bd7630032dd71dfa4d` (**P0258**, documented acquisition): the Phoenix panel shows similarity 0.59, **no shared identifiers**, and *Documented acquisition REL00423 · may legitimately explain the overlap*. Next evidence → **Request evidence** (ownership records) → tab **Requests** → *Verified · explains the finding* with notes → **Record reviewed outcome**. Result: Phoenix finding *EXPLAINED* (original 29 evidence records kept, +1 review record); the unrelated ML anomaly stays active; priority 65.6 → **60.2** (queue #646 of 898); timeline: *evidence request created → evidence outcome reviewed → finding resolution*.

## Limits to state during the demo

Synthetic data and constructed scenarios; manually chosen thresholds and weights; probabilities are synthetic default assumptions (no evidence-specific history); no diagnoses; geography is synthetic and uninformative in the base data; member responses are simulated; a successor link is a reason to investigate, not a finding of control or misconduct.
