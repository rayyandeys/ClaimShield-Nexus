# ClaimShield Nexus — Screen Guide

What every screen shows and does, where Groq is used, and where each engine and feature can be viewed, tested and demonstrated.

- App: http://localhost:5173 · API: http://localhost:8000 · API docs: http://localhost:8000/docs
- Example IDs are real records from the local demo database (after `cli augment` + `cli analyze`). Content-hash IDs are identical on any machine with the same setup; queue positions can shift as reviews are recorded.
- All records are synthetic. Scores are review priorities or similarity measures, never fraud probabilities.

---

## 1. Navigation at a glance

| Sidebar item | Route | Purpose |
|---|---|---|
| Overview | `/` | **Live Claims Monitor** (synthetic claim stream) and the dashboard of imported data, findings, cases and system readiness |
| Data ingestion | `/ingestion` | Import, validate and analyze data; job queue |
| Claims explorer | `/claims`, `/claims/:id` | Search claims and open one claim with its findings |
| SupplyTrace | `/supplytrace`, `/supplytrace/:id` | Itemized supply billing compared with peers |
| Nexus network | `/network?entity=…` | Relationship graph, including Phoenix successor links |
| Member radar | `/radar`, `/radar/:providerId` | Suspicious new-member batches and member risk |
| Future risk | `/forecast?provider=…` | 30/60/90-day forecasts and the anomaly model for a provider |
| Smart SIU queue | `/queue` | Ranked investigation cases within investigator capacity |
| (from the queue) | `/cases/:id` | Case Workspace — the main investigation screen |
| Models & evaluation | `/evaluation` | Model cards, held-out metrics and scenario results |

Every page has loading, empty and error states; tables paginate.

---

## 2. Screens

### 2.1 Overview (`/`)
| Section | What it shows / does |
|---|---|
| Header buttons | *Manage data* → Data ingestion; *Open SIU queue* → queue |
| **Live Claims Monitor** | First card. **Start Live Simulation** (rate 0.5/1/2 per s, 30–120 claims, *Mixed demo* or *Ordinary only*) and **Stop Simulation** call the backend. The session strip shows status, scenario pack and seed, a session picker for earlier sessions, and *Clear display* (hides entries; deletes nothing). Twelve server-side counters: generated, accepted, analyzed, fully analyzed, pending, failed, new findings, cases, rate, backlog, micro-batch, latest analysis. The **event feed** holds committed backend events and is searchable and filterable (Claims / Findings & cases / Enrichment / Failures). Clicking an event opens its Claim detail, SupplyTrace, Case Workspace, Member radar, Nexus network or Future risk view. The **pipeline inspector** (scan icon, or a row in *Recent claims*) shows all 12 stages with status, time, findings, reason or error, and the *Fully analyzed* badge only when the backend reports it. See [LIVE_STREAM_DEMO.md](LIVE_STREAM_DEMO.md). |
| Dataset strip | Service-date range, provider and facility counts |
| Four stat cards | Imported claims, active findings, active investigations, potential review exposure (unique claims; upper bound, not confirmed loss) |
| Claims activity | Monthly claim volume chart |
| Investigation priority | Cases by severity (donut chart) |
| Your next investigations | Top 4 queue cases; click a row to open the case |
| System readiness | Model status (Isolation Forest, 30/60/90 forecasts) and **Groq copilot: CONFIGURED / OFFLINE** |
| Recent processing runs | Latest import and analysis batches |

### 2.2 Data ingestion (`/ingestion`)
| Section | What it shows / does |
|---|---|
| *Import local dataset* | Queues an import of `data/synthetic` through the worker |
| *Import & analyze* | Queues import + full analysis (all engines, model training, consolidation, ranking, evaluation) |
| Dataset validation | Latest import audit status, row counts, validation issues (first 100) |
| Upload a CSV snapshot | Upload all 12 operational CSVs (optionally the 2 profile files) as one snapshot |
| *Run analysis on imported data* | Re-runs analysis without importing |
| Persistent job queue | Job status, progress, attempts, errors |
| Import & analysis history | All batches |

The scenario pack is added from the command line: `docker compose exec backend python -m app.cli augment`, then `… cli analyze`.

### 2.3 Claims explorer (`/claims`) and claim detail (`/claims/:id`)
| Section | What it shows / does |
|---|---|
| Filters | Search (claim, member, procedure), provider ID, date range; pagination |
| Claim header buttons | *Explore network* (provider graph), *Open SupplyTrace* |
| Financial cards | Billed, allowed, paid, member responsibility (kept separate) |
| Related investigations | Links to cases that include this claim |
| Procedure lines | Line items |
| Screening findings | Every finding on the claim with engine, severity, explanation, evidence IDs and limitations |
| Supporting encounter | Encounter record, or a "no encounter — request documentation" notice |
| Complete source claim | All raw claim fields |

### 2.4 SupplyTrace (`/supplytrace`, `/supplytrace/:id`)
| Section | What it shows / does |
|---|---|
| List | Claims that have itemized supplies |
| Itemized consumables | Each supply with quantity, unit price and **peer medians** |
| Per-item cards | Bar chart of this claim vs peer median; peer group (procedure, complexity, peer count); full comparison details |
| Estimate to final bill | Pre-procedure estimate vs final billed amount |

### 2.5 Nexus network (`/network?entity=…`)
| Section | What it shows / does |
|---|---|
| Filters | Entity ID + *Explore*, entity types, relationship types (incl. **possible successor**, practice acquisition), as-of date |
| Successor banner | Appears when the provider has a flagged possible-successor link: predecessor, status, similarity, links to case and graph edge |
| Graph | Cytoscape graph: providers (circles), facilities (squares), owners (diamonds), claims/members; **dashed orange edges = possible successor (similarity, not a recorded relationship)**; zoom/fit controls |
| Evidence inspector | Click a node or edge to see its source record. Clicking a dashed successor edge shows the **Phoenix breakdown**: score, five components, matched identifiers, shared patients/codes/referrers, documented acquisition, predecessor history (context only), link to case |
| Related investigations | Cases that involve the entity |

### 2.6 Member radar (`/radar`) and batch detail (`/radar/:providerId`)
| Section | What it shows / does |
|---|---|
| New-member batch candidates | Providers with ≥10 newly billed members in the last 90 days: flagged / not flagged, new members, growth, % without prior relationship, % with prior care context, dispersion; search and filter; click to open |
| Member risk ranking | Members ranked by robust score with all five signals and coverage |
| Batch header | Analysis windows, detector version; *Open consolidated case*, *Nexus network*; successor banner if applicable |
| Threshold checks | Each check with observed value, threshold, required/corroborating role, passed / not met / unavailable, plus notes |
| Historical vs recent acquisition | Distinct members per month (recent window highlighted) |
| Provider–member network | Provider plus members colored by status: billed before window, new with/without prior relationship, pending confirmation, simulated denial (triangle), simulated confirmation (square) |
| Member inspector | Click a member (graph or table): relationship, distance, the five signals with value, robust z and coverage |
| Newly billed members | Paginated table with filters (no prior relationship / prior relationship / prior care context), claim links, confirmation status |

### 2.7 Future risk (`/forecast?provider=…`)
| Section | What it shows / does |
|---|---|
| Provider selector | Any provider |
| Successor banner | If the provider has a flagged successor link |
| 30 / 60 / 90-day cards | Probability of defined observable billing events (not fraud), held-out PR-AUC, signals and limitations |
| Provider service history | Monthly claims and allowed amounts |
| Local anomaly model | Isolation Forest historical percentile, raw score (not a probability), observable features |

### 2.8 Smart SIU queue (`/queue`)
| Section | What it shows / does |
|---|---|
| Investigator capacity | Slider / number (1–100) — the queue shows the top K cases |
| Evidence that could change this queue | Next-Best-Evidence summary: best evidence request for cases near the capacity boundary, decision-change probability, boundary flag (hypothetical) |
| Filters | Search, provider, status, severity |
| Ranked table | Priority, severity, review exposure, workflow (pre/post-payment), status, assignee; click a row to open the case |

### 2.9 Case Workspace (`/cases/:id`)
The main investigation screen.

**Header and summary**
| Section | What it shows / does |
|---|---|
| *Challenge evidence* button | Evidence Challenger (**Groq**, falls back to a deterministic report) |
| *Generate brief* button | Investigation brief (**Groq**, falls back to a deterministic report) |
| Status badges | Case status, severity, payment workflow, assignee, claim/finding counts |
| Summary | Review priority /100, potential review exposure (unique claims, not confirmed loss) |
| Successor banner | If the case's provider has a flagged successor link |

**Tabs**
| Tab | What it shows / does |
|---|---|
| Findings | Possible-successor panel (if any) with the five components; every finding as a card (large groups collapsed, e.g. "82 member id possibly compromised findings"); *Review this finding*: select evidence, decision (explained / unresolved / escalated), reason → saves and recalculates priority |
| Next evidence | Top three evidence requests: what each outcome would mean, current vs hypothetical priority and queue position for "explains" and "supports", probability source label, days, decision-change probability; buttons **Request evidence**, **Simulate explains finding**, **Simulate supports concern** (yellow dashed *HYPOTHETICAL — nothing was saved* preview with cases entering/leaving the top K); adjustable capacity K |
| Requests | Evidence requests with status; **Record reviewed outcome** (received, verified explains, verified supports, inconclusive, withdrawn) with notes |
| Confirmations | Shown for member-batch / member / missing-encounter cases. Simulated notices with the generated text; **Yes / No / I'm not sure** simulated answers; review form after an answer; table of eligible claims with **Generate simulated notice** |
| Spillover | Shown for member-batch cases. Other claims of the affected members: totals, groups by provider / claim type / month, recent claims; leads only, not exposure |
| Evidence | Every source evidence record with original values and reference context |
| Claims | Related claims with links to the claim and SupplyTrace |
| Brief | Output of Generate brief / Challenge evidence / Evidence investigator: AI-ASSISTED or DETERMINISTIC label, verification status, warning (incl. discarded AI items), facts, alternatives, actions, limitations |
| Timeline | Append-only audit trail: every action, actor, time, explanation, state change |

**Right-hand panel**
| Section | What it shows / does |
|---|---|
| Priority breakdown | The six SIU ranking factors (severity, evidence, exposure, member impact, recurrence, independent channels) |
| Investigator controls | Add note, change status (valid transitions only), assign investigator, request records |
| Connected context | Links to Nexus network, Future risk, Member Radar batch (if any) and the **Evidence investigator** button (**Groq**) |

### 2.10 Models & evaluation (`/evaluation`)
| Section | What it shows / does |
|---|---|
| Model cards | Isolation Forest and 30/60/90 forecasts: status, PR-AUC, ROC-AUC, Brier, Precision@K, calibration chart, features, splits, limitations |
| Held-out detection comparison | Rules only vs + ML vs + graph + SupplyTrace on held-out synthetic claims |
| Controlled brief verification | Results of the mocked Groq tests (valid, malformed, invented evidence, rate limit, timeout) |
| Scenario pack evaluation | Member Radar and Phoenix vs the scenario manifest: expected vs detected, agreement, unexpected flags |

---

## 3. Where Groq is implemented

| Layer | Location |
|---|---|
| UI buttons | Case Workspace: **Generate brief**, **Challenge evidence** (header), **Evidence investigator** (Connected context) → output in the **Brief** tab |
| Status indicator | Overview → System readiness → *Groq copilot: CONFIGURED / OFFLINE* (`GET /api/v1/models/status`) |
| API routes | `POST /api/v1/cases/{id}/brief`, `/challenge`, `/investigate` (`backend/app/main.py`) |
| Logic | `backend/app/services/briefs.py`: `compact_context` (bounded request), per-mode prompts, strict JSON schema, one retry on schema failures, `verify_synthesis` (drops unverifiable items), deterministic `fallback`, caching of successful results only |
| Configuration | `GROQ_API_KEY`, `GROQ_MODEL` (default `openai/gpt-oss-120b`), `GROQ_TIMEOUT_SECONDS`; passed through `docker-compose.yml`. A Windows user environment variable overrides `.env` |
| Key check | `docker compose exec backend python -m app.cli groq-check` (lists models; no generation) |
| Tests | `backend/tests/test_briefs.py` (verification rules) and Groq tests in `backend/tests/test_integration.py` (mocked; the test script blanks the key so tests never call Groq) |

Groq never decides anything: all detection, scoring, simulations and resolutions are deterministic. Without a key, every Groq button returns a labeled deterministic report.

---

## 4. Where each engine and feature can be viewed, tested and demonstrated

| Engine / feature | Where to view in the UI | Example to demonstrate | Code | Tests |
|---|---|---|---|---|
| Data ingestion & validation | Data ingestion | Validation card → issues (130 missing encounters + 270 scenario claims as warnings) | `services/ingestion.py` | `tests/test_ingestion.py` |
| Rules — duplicate billing | Claim detail → Screening findings; case Findings | Claim `C0000053` · case `CASE-eee1d29c6789e723890b` | `services/detection.py` (`rules`) | `tests/test_detection.py` |
| Rules — impossible timing | same | Claim `C0001156` · `CASE-43c56302353f47dfd5d1` | same | same |
| Rules — upcoding | same | Claim `C0000355` · `CASE-e23449bc756c625ce872` | same | same |
| Rules — unbundling | same | Claim `C0000228` · `CASE-5dac5b130f922ad8e51c` | same | same |
| Rules — early refill | same | Claim `C0000097` · `CASE-c97beba006ee7c919976` | same | same |
| Rules — missing encounter | same (claim shows "No encounter is available") | Claim `C0000112` · `CASE-f37e704870c02c726c88` | same | same |
| SupplyTrace — quantity anomaly | SupplyTrace → claim | `/supplytrace/C0009835` (syringes 26 vs peer threshold 14); also `C0005906` | `services/detection.py` (`supply_analysis`) | `tests/test_detection.py` |
| SupplyTrace — estimate drift | SupplyTrace → Estimate to final bill | Claim `C0003965` · `CASE-a05202d5a9342c6fda3d` | same | same |
| ML anomaly (Isolation Forest) | Future risk → Local anomaly model; case Findings | `/forecast?provider=P0124` | `services/ml.py` | `tests/test_graph_cases_ml.py`, integration |
| 30/60/90-day forecasts | Future risk cards; Models & evaluation | `/forecast?provider=P0124` | `services/ml.py` | same |
| Nexus graph | Nexus network | `/network?entity=P0141` | `services/graph.py` | `tests/test_graph_cases_ml.py` |
| Case consolidation & SIU ranking | Smart SIU queue; case Priority breakdown | Top cases: `CASE-27cd5d9b606a132d854e`, `CASE-57780afce8a6f1931ba5` | `services/cases.py` | `tests/test_graph_cases_ml.py`, integration |
| Human review & audit | Case → Findings (*Review this finding*), Investigator controls, Timeline | Any case | `services/cases.py` | `tests/test_integration.py` |
| **Next-Best-Evidence** | Case → *Next evidence*, *Requests*; queue card | `CASE-27cd5d9b606a132d854e` (#1 ownership records; explains → #25) | `services/evidence.py`, `config/evidence_catalog.json` | `tests/test_features.py`, `tests/test_features_integration.py`, `frontend/src/features.test.tsx` |
| **Member Radar** | Member radar page and batch detail | `/radar/P0252` (flagged) vs `/radar/P0259` (not flagged) | `services/radar.py`, `config/detectors.json` | same |
| **Member confirmation loop** | Case → *Confirmations*; Timeline | `CASE-27cd5d9b606a132d854e` (269 claims still eligible) | `services/evidence.py` | same |
| **Spillover** | Case → *Spillover* | `CASE-27cd5d9b606a132d854e` (1,376 related claims) | `services/evidence.py` (`spillover`) | same |
| **Phoenix detector** | Nexus network dashed edge + inspector; case Findings panel; banners | `/network?entity=P0252` (P0251 → P0252, 0.82); subtle pair `/network?entity=P0254`; look-alike `CASE-a2bd7630032dd71dfa4d` (P0258) | `services/phoenix.py`, `services/graph.py` | same |
| Scenario evaluation | Models & evaluation → Scenario pack evaluation | 6/6 scenarios agree | `services/pipeline.py`, `scenarios.py` | `tests/test_features_integration.py` |
| Groq brief / challenger | Case → Generate brief / Challenge evidence → Brief tab | `CASE-27cd5d9b606a132d854e` | `services/briefs.py` | `tests/test_briefs.py`, `tests/test_integration.py` |
| **Live Claims Monitoring** | Overview → Live Claims Monitor; claim detail banner; SIU queue; Member radar `PS000kC` | Start a 60-claim session. Duplicate SC000k-0005, supply outlier SC000k-0008, overlap SC000k-0011, radar batch PS000kC. Measured run: STREAM-0003 (cases `CASE-853dd1eafe6eda995f4f`, `CASE-cd0f44fbea61a886ce6c`, `CASE-3e72e0feb311c808edf2`) | `services/stream.py`, `services/stream_generator.py`, `stream_runner.py`, `worker.py` (`stream_enrichment`) | `tests/test_stream.py`, `tests/test_stream_integration.py`, `frontend/src/stream.test.tsx` |

### Running the tests
```powershell
./scripts/test.ps1                                      # everything: backend, feature workflow, frontend
docker compose exec backend python -m app.smoke         # read-only live API smoke test
docker compose exec backend python -m app.cli groq-check
```

### Full demo
Live stream first: [LIVE_STREAM_DEMO.md](LIVE_STREAM_DEMO.md). Then follow [demo_script.md](demo_script.md): Member Radar → Phoenix → consolidated case → Next-Best-Evidence simulation → member confirmations → spillover → human decision and audit → false-positive safeguards.
