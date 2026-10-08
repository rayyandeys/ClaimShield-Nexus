# Live stream demo — judge walkthrough

A 60–90 second live demonstration, followed by the original investigation demo. The IDs and timings below come from the measured session **STREAM-0003** (2026-10-08, local Docker stack, 60 claims at 1/s).

Every new session gets a new number *k*: providers `PS{k}A/B/C`, claims `SC{k}-0001…`. The scenario order is identical for the same seed, so the claim with sequence 5 is always the duplicate and sequence 8 is always the supply outlier.

All records are synthetic. The stream adds new records only; it never changes the P0252/P0258 scenario records.

---

## 0. Start the application

```powershell
docker compose up -d                 # db, backend (applies migration 0003), worker, stream, frontend
docker compose ps                    # all five services healthy
```

First time on a fresh machine (otherwise skip), see the [demo setup](demo_script.md#setup-fresh-machine):

```powershell
docker compose exec backend python -m app.cli all
docker compose exec backend python -m app.cli augment
docker compose exec backend python -m app.cli analyze
```

There is no separate preparation step for the stream. Each session prepares its own cohort when it starts: 3 new synthetic providers in Seattle, 30 brand-new members, and about 29 existing members with care history.

Open http://localhost:5173. The **Live Claims Monitor** is the first card on **Overview**.

---

## 1. Initial dashboard (Stage 1)

- **Overview** shows the analyzed dataset: imported claims, active findings, investigations and review exposure.
- The **Live Claims Monitor** shows *Latest session* (the last finished one), or *No session yet* on a fresh database.
- The runner must be up. If the card says *stream runner service is not running*, run `docker compose up -d stream`.

## 2. Start streaming (Stage 2)

1. Leave **Rate 1/s**, **Claims 60** and **Mode Mixed demo** as they are, then click **Start Live Simulation**.
2. Within about 1 s the strip shows `STREAM-000k · running` and the scenario-pack line `live-stream-pack-v1 · mixed demo · seed 20261010`.
3. Counters rise from committed server state: *Generated → Accepted → Analyzed* (immediate screening). *Fully analyzed* lags behind, because it waits for the background micro-batch.

Double-clicking Start is safe: the button is disabled while a session is active, and the API returns 409 for a second start.

## 3. Ordinary claim (Stage 3)

- **Feed:** `claim persisted`, then `rules completed — 0 finding(s)`, then `supplytrace not applicable`.
- **Click** the `#4 SC000k-0004 committed to PostgreSQL` event. The real **Claim Detail** page opens with a green *Live-stream synthetic claim* banner, stored lines and encounter, and *no findings*.
- **Inspector:** back on Overview, click the scan icon on that event. All 12 stages show, ending *Fully analyzed* once its micro-batch has run.
  - Isolation Forest shows the provider's percentile.
  - Member Radar shows "not a new-member batch candidate".
  - Phoenix shows the pairs it evaluated.
  - Case consolidation and SIU ranking show *not applicable*: an ordinary claim is not placed in a case.

In STREAM-0003, SC0003-0004 and SC0003-0003 (a procedure with ordinary supplies) stayed finding-free after all engines.

## 4. Duplicate concern (Stage 4) — about 4 s after start

- **Feed:** `finding created · rules finding · duplicate billing · HIGH: 2 separate submissions share member, provider, procedure and service timestamp`, then `case created`, then `priority recalculated`.
- **Click** the finding. It opens Claim Detail for SC000k-0005, where the finding lists the source claim pair (#1 and #5) and the case link.
- **Click** the `case created` event. The **Case Workspace** opens (STREAM-0003: `CASE-853dd1eafe6eda995f4f`, PREPAYMENT, pending claims). The timeline shows *case created* by *Live stream processor*.

## 5. Supply anomaly (Stage 5) — about 8 s after start

- **Feed:** `finding created · supplytrace finding · consumable quantity anomaly · HIGH: Bandages: quantity 160.00 exceeds matched-peer threshold 63.25`.
- **Click** it. The **SupplyTrace detail** page for SC000k-0008 shows the peer comparison:
  - BANDAGE 160 against a threshold of ≈ 63 (542 matched peer items).
  - GLOVE 14 and SYRINGE 4 within range.
- STREAM-0003 case: `CASE-cd0f44fbea61a886ce6c`.

About 10 s in, `impossible timing · HIGH` appears for SC000k-0011, whose appointment overlaps #10's for a different member. That updates the duplicate case (`case updated`).

## 6. Background enrichment (Stage 6)

- **Feed** (filter *Enrichment*): `enrichment scheduled`, then `enrichment started`, then per-provider `isolation forest / nexus graph / member radar / phoenix / forecast … completed`, then `enrichment completed in ~3.3–4.0 s`, then `claim fully analyzed` events.
- **About 21 s:** Isolation Forest flags the three new cohort providers at the 96–98th percentile. This is the cold-start effect of a provider ramping up from zero volume; see the limitations.
- **About 53 s:** the micro-batch reports `Member Radar · PS000kC: 30 new members, growth 30.0×, 100% without prior relationship, 0% with care context → QUALIFIED batch`. The `stolen id batch` finding follows. Clicking the radar event opens **Member radar → PS000kC**, with the threshold checks and the member list.
- **Contrast:** `PS000kA: 14 new members … 100% with care context → not qualified`. Its new patients have prior care for the same service, so the same detector does not flag it.

## 7. Case and ranking update (Stage 7)

Open **Smart SIU queue**. STREAM-0003's cases took their actual calculated places; none was forced to #1:

| Case | Title | Priority | Queue |
|---|---|---|---|
| CASE-cd0f44fbea61a886ce6c | PS0003B · consumable quantity anomaly | 75.45 | #16 |
| CASE-853dd1eafe6eda995f4f | PS0003A · duplicate billing (+ overlap, ML) | 71.03 | #30 |
| CASE-3e72e0feb311c808edf2 | PS0003C · provider anomaly + member batch | 68.32 | #72 |

`CASE-27cd5d9b606a132d854e` (P0252) remains #1 at 86.72.

## 8. Next-Best-Evidence (Stage 8)

Open the duplicate case, then the **Next evidence** tab. The recommendations are computed from its current findings:
- *Medical record sample* (provider anomaly): explains → 65.05, #317.
- *Corrected or replacement claim record* (duplicate billing).

**Simulate explains finding** shows *HYPOTHETICAL — nothing was saved*.

## 9. Stop and drain (Stage 9)

- Click **Stop Simulation** at any time. The feed shows `stop requested — no further claims will be generated` and then `generation stopped … draining N claim(s)`. *Generated* stops increasing, while *Fully analyzed* keeps rising until *Pending* reaches 0.
- A full 60-claim run ends by itself with `session completed: 60 accepted, 60 fully analyzed, 0 failed, 0 rejected`, and the status badge reads **completed**. STREAM-0003 finished 65 s after start.

**Verify that every accepted claim finished:**
- The *Pending* counter is 0 and *Failed* is 0, or
- `curl http://localhost:8000/api/v1/stream/sessions/STREAM-000k/summary` returns `metrics.fully_analyzed == metrics.accepted` and `stage_counts` without PENDING, RUNNING or FAILED rows.

## 10. Continue the original investigation demo (Stage 10)

Follow [demo_script.md](demo_script.md) stages 1–8. Everything still works, and the stream did not change any of those records (verified by content hashes before and after: findings, audit events, cases, evidence requests, confirmations).

**Note for the Member radar list:** each completed stream session adds its own qualified cohort batch (`PS000kC`, growth 30×), which sorts above P0252 (8.6×). Type `P0252` in the radar search box, or open http://localhost:5173/radar/P0252 directly.

---

## Running another demonstration

Click **Start Live Simulation** again once the previous session has completed or stopped. Each replay:
- uses the same seed and scenario order,
- gets new session-specific IDs and a fresh cohort (new providers, new members, and existing members not used by earlier sessions),
- never collides with earlier sessions: no false duplicates against them, and no primary-key conflicts.

The previous sessions stay in the database and remain selectable in the session picker. **Clear display** only hides feed entries; nothing is deleted.

## Measured run (optional, for the numbers)

```powershell
docker compose exec backend python -m app.stream_measure --claims 60 --rate 1
```

This starts a real session, samples the metrics and dashboard API latency once per second, and writes `artifacts/evaluation/live_stream_measurement.json`.
