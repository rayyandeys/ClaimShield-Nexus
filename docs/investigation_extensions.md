# Investigation extensions: Next-Best-Evidence, Member Radar, Phoenix

Three features added on top of the existing pipeline. All detection, similarity, probability and simulation logic is deterministic and works without Groq. All data is synthetic; nothing contacts a real person.

## Data: scenario pack v1

`python -m app.cli augment` (module `backend/app/scenarios.py`, seed `20261009`) copies the 12 original CSVs **unchanged**, appends new rows with new IDs and adds two optional files, then imports the result with the existing transactional importer. Existing rows are identical, so only new IDs are inserted.

| Appended | Rows |
|---|---:|
| providers (P0251–P0264) | 14 |
| claims / claim lines | 1,151 / 1,151 |
| encounters | 881 |
| referrals | 295 |
| relationships (directory + one `practice_acquisition`) | 15 |
| investigation history | 1 |
| `provider_profiles.csv` (all providers: synthetic address, ZIP token, coordinates, `SYN-555-…` phone, practice owner, `SYNTH-BANK-TOKEN-…`, operating status, enrollment date) | 264 |
| `member_profiles.csv` (synthetic home coordinates) | 4,000 |

- Output: `artifacts/scenarios/v1/snapshot/` (git-ignored, reproducible; checksums in the manifest).
- Scenario labels live only in `artifacts/scenarios/v1/scenario_manifest.json`. Detection never reads it; the pipeline compares results with it afterwards (`artifacts/evaluation/scenario_report.json`).
- All scenario activity is dated after 2025-10-01, so the Isolation Forest and forecast training snapshots are unchanged. Verified: on the original records, the 26 anomaly flags and Precision@100 (0.39 / 0.59 / 0.92) are identical before and after augmentation.
- Without the profile files both detectors degrade gracefully: geography is reported unavailable and no Phoenix pair is eligible.

Scenarios: stolen-ID batch at P0252 (270 previously unrelated, dispersed members, catheter supplies, no encounters, six weeks); legitimate look-alike P0259 (absorbs a closing local practice's referred patients); Phoenix obvious pair P0251→P0252 (shared owner/phone/bank token, 23 days); subtle pair P0253→P0254 (no shared identifiers); address-only look-alike P0255→P0256; documented acquisition P0257→P0258.

## Feature 1 — Next-Best-Evidence

- Catalog: `backend/app/config/evidence_catalog.json` (versioned), keyed by the existing finding taxonomy (all 16 finding types). Default probabilities are **synthetic assumptions**.
- Probability: Beta(1,1)-smoothed share of reviewed outcomes that explained the finding, once at least 5 reviewed outcomes exist for that finding type and evidence type. Otherwise the default is used and labeled `synthetic_default_assumption` (n=0) or `insufficient_history` (n<5). `investigation_history.csv` has no finding/evidence types, so it cannot supply evidence-specific rates.
- Scoring: every hypothetical uses the production scorer `cases.rank_values` on the same inputs as `cases.recalculate` (`cases.case_inputs`), with outcomes applied by `cases.apply_outcome`:
  - *explains*: finding status `EXPLAINED` (removed from active scoring, as in human resolution);
  - *supports*: status `ESCALATED`, `data_completeness` → 1.0 (the scorer's existing evidence-strength proxy); a `stolen_id_batch` becomes HIGH only after 3 distinct reviewed member denials.
- Queue position: stored priorities of every queue-eligible case, ordered `priority desc, case_id asc` (the SIU queue order); score 0 or closed means not queued.
- Ranking: `decision_value = expected_absolute_priority_shift × (0.3 + 0.7 × P(review decision changes at capacity K)) / max(days, 0.5)`. The spec's signed heuristic (`legacy_voi`) and the directed delta are reported separately; absolute shift is used because evidence that lowers a score can be just as decision-relevant.
- Simulations and recommendations run in PostgreSQL `READ ONLY` transactions; the integration suite hashes every relevant table before and after.
- Lifecycle: `REQUESTED → RECEIVED → VERIFIED_EXPLAINS | VERIFIED_SUPPORTS | INCONCLUSIVE | WITHDRAWN`. A verified outcome adds a source-linked evidence record (earlier evidence is never modified), then uses the existing `resolve` or the new `corroborate` (both recalculate priority and append audit events). Duplicate open requests return the existing one unless a justification is given; repeating the same outcome is idempotent.

## Feature 2 — Member Radar and confirmation loop

Config: `backend/app/config/detectors.json` → `member_radar` (version `member-radar-1.0`). As-of date = latest service date; recent window 90 days; baseline 365 days before it.

- Member signals: A first-contact burst (30 days); B care-history mismatch (proxy: no prior same-code claim, same-provider claim or referral — diagnoses unavailable); C geographic jump (offline haversine); D missing-encounter rate; E cross-provider duplicates (same code, different providers, ≤7 days). Score = mean of robust z (median/MAD with a documented minimum scale) clipped to 0–5; at least two evaluable signals required; coverage reported.
- Batch checks (prototype heuristics): ≥25 new members, ≥5× historical acquisition rate (normalized by active baseline days), ≥80% without a prior relationship, ≤20% with prior care context — all required; geographic dispersion percentile >0.75 is **corroborating, not required**, because in the base synthetic data only ~10% of claims are in the member's home state.
- Relationships are evaluated strictly before each member's first recent claim (referrals on or before); records created during the burst never count.
- Findings: `stolen_id_batch` (provider entity, bounded evidence: summary, profile and 20 sample claims; full member list in `radar_batch_members`, paginated) and `member_id_possibly_compromised` for batch members with member score ≥1.5.
- Confirmations: deterministic notice from the stored claim, provider and date, prominently labeled simulated; never asks for credentials, IDs or banking details. Only the claim's own member can be addressed. A response moves the linked evidence request to `RECEIVED`; nothing changes scores until an investigator reviews it. NO → may be reviewed as supporting; YES → may explain only that member's allegation (never the batch); NOT SURE → inconclusive only. Distinct members count once.
- Spillover: other claims of the batch members, grouped by provider, claim type and month with billed / allowed / paid kept separate. Leads only — never added to exposure, never blocks payment.

## Feature 3 — Phoenix Provider Detector

Config: `detectors.json` → `phoenix` (version `phoenix-1.0`). Predecessor: suspended / revoked / closed with an effective date. Candidates: providers enrolled within 180 days after that date (sorted enrollment index), with ≥5 claims in their first 90 days.

Components: patient overlap (Jaccard, predecessor's last 180 days vs successor's first 90), shared identifiers (exact owner / bank token / phone; address fuzzy only when house numbers match), billing fingerprint (cosine over code counts), referral-source reuse (Jaccard), timing (1 − days/180). Weights 0.35 / 0.25 / 0.20 / 0.15 / 0.05. Flag when score ≥0.55, at least two non-timing components ≥0.30 and at least one behavioral component ≥0.30. Empty sets and missing identifiers are unavailable, never matches.

Flagged pairs create a `phoenix_successor` finding on the successor citing only its own records and the comparison evidence (predecessor findings are never copied; prior investigations are context only) and a `possible_successor` dashed edge in the existing Nexus graph (`verification_status: algorithmic_similarity`). Links are upserted by a stable pair ID.

## Integration

Pipeline order: rules → SupplyTrace → ML → graph → **Member Radar → Phoenix** → persist findings / evidence → detector tables → consolidation → ranking → evaluation. Consolidation adds one rule: provider-level findings about the same provider (anomaly, member batch, possible successor) join one case. Exposure remains unique-claim based.

Two correctness fixes found while integrating: ML specialty peer medians now include only providers active by the cutoff (previously not-yet-existing providers entered as zero-volume peers; no effect on the original data), and Phoenix address matching requires equal house numbers.

## Endpoints (`/api/v1`)

`GET cases/{id}/next-evidence?capacity=` · `GET siu/queue/next-evidence?capacity=` · `POST cases/{id}/simulate` · `GET|POST cases/{id}/evidence-requests` · `POST evidence-requests/{id}/outcome` · `GET|POST cases/{id}/confirmations` · `POST confirmations/{id}/response` · `GET cases/{id}/spillover` · `GET radar/batches`, `radar/batches/{provider}`, `…/members`, `…/graph`, `radar/members`, `members/{id}/risk` · `GET phoenix/links`, `phoenix/links/{id}`, `providers/{id}/successors`, `providers/{id}/predecessors`. Errors: 404 unknown record, 422 invalid input or inapplicable evidence, 409 state conflict.

## Limits

Thresholds, weights and probabilities are prototype heuristics on constructed synthetic scenarios; agreement with the scenario manifest is not evidence of real-world performance. Member confirmations are simulated. Analysis reruns on a changed source population can leave earlier findings from a superseded model version active (pre-existing limitation).
