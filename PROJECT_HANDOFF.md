
# CLAIMSHIELD NEXUS — PROJECT HANDOFF & RECOVERY GUIDE

**Project:** ClaimShield Nexus
**Team:** CIPHER
**Type:** Healthcare Fraud, Waste & Abuse (FWA) Investigation Platform
**Environment:** Local-first, Docker-based
**Development:** VS Code + Codex
**LLM Provider:** Groq API
**Purpose:** Continue development safely on another computer or in a fresh AI coding session.

---

# 1. INSTRUCTIONS TO THE AI ASSISTANT

You are taking over development of an existing project called ClaimShield Nexus.

Act as a senior software architect, full-stack engineer, machine-learning engineer, and QA engineer.

IMPORTANT:

THIS PROJECT HAS ALREADY BEEN PARTIALLY OR SUBSTANTIALLY IMPLEMENTED.

DO NOT START BUILDING IT FROM SCRATCH.

DO NOT ASSUME THAT WORK REPORTED IN THIS DOCUMENT IS NECESSARILY PRESENT IN THE CURRENT CHECKOUT.

YOUR FIRST TASK IS TO INSPECT THE ACTUAL REPOSITORY AND DETERMINE:

1. What has already been implemented.
2. What has been successfully tested.
3. What is implemented but not verified.
4. What is incomplete or missing.
5. What is broken.
6. What must be configured on this computer.
7. What should be done next to finish the project.

Treat repository source code, tests, existing documentation, model artifacts, and actual execution results as the primary sources of truth.

Treat historical progress notes below as context, NOT proof that the current checkout is fully functional.

DO NOT immediately rebuild existing components.

DO NOT fabricate results.

DO NOT say a component works merely because a file with its name exists.

DO NOT substitute placeholder screens, hardcoded results, or simulated AI outputs for actual implementation.

Start with a thorough project audit, then provide a detailed status report.

Do not begin major architectural changes until that report is complete.

---

# 2. WHAT THIS PROJECT IS

ClaimShield Nexus is an AI-assisted healthcare claims investigation platform.

Tagline:

"From Suspicious Claims to Defensible Investigations"

It uses synthetic healthcare insurance records to identify potentially suspicious billing behavior, analyze provider networks, estimate future risk, consolidate related findings, and prioritize investigations.

It combines:

- Deterministic claim rules
- SupplyTrace itemized billing analysis
- Machine-learning anomaly detection
- Healthcare relationship graph analytics
- 30/60/90-day recurrence-risk forecasting
- Evidence fusion and case consolidation
- Capacity-aware Special Investigations Unit (SIU) ranking
- Groq-powered investigation assistance
- Evidence Challenger
- Human investigator decisions
- Persistent audit trails

It is a decision-support prototype.

It must never automatically declare a person, provider, or facility guilty of fraud.

Only public or synthetic healthcare information is permitted.

---

# 3. HISTORICAL DEVELOPMENT STATUS

The previous Codex session reported the following progress.

THESE ARE HISTORICAL REPORTS, NOT INDEPENDENTLY VERIFIED FACTS ABOUT THIS CHECKOUT.

## 3.1 Dataset inspection

Previous Codex reported:

- 20,000 synthetic healthcare claims
- 27,221 claim line items
- 7,081 supply items
- 250 providers
- 60 healthcare facilities
- Historical period: October 2024 to September 2026
- 12 operational CSV datasets
- Separate synthetic ground-truth evaluation data

Two known dataset limitations were reported:

1. Approximately 130 claims lacked encounter references.
2. Every correction reference was empty.

Missing encounters were intended to produce documentation-gap indicators rather than automatic fraud conclusions.

Correction handling was intended to be tested using controlled fixtures rather than represented as real records in the original dataset.

Reinspect the CSVs to verify the actual counts and limitations.

## 3.2 Database and infrastructure

The previous Codex session reported:

- Four Docker services healthy
- PostgreSQL running
- Database migration successful
- Transactional import completed
- All 12 operational CSV datasets imported
- API serving imported records
- Background worker functioning

Do not assume those Docker containers or their database data exist on this new machine.

Docker named volumes are local and normally do not transfer through GitHub.

## 3.3 Detection pipeline

The previous Codex session reported one completed full analysis run:

- Runtime: 36.3 seconds
- Findings generated: 1,510
- Consolidated investigation cases: 894

Verify these values against actual records or rerun the documented analysis.

Do not treat these counts as guaranteed on a fresh installation.

## 3.4 Machine-learning models

The previous session reported successful training of:

1. Isolation Forest anomaly-detection model
2. 30-day recurrence-risk forecasting model
3. 60-day recurrence-risk forecasting model
4. 90-day recurrence-risk forecasting model

Reported held-out forecast PR-AUC values:

- 30-day: approximately 0.070
- 60-day: approximately 0.145
- 90-day: approximately 0.214

These are historical results, not new measurements.

They are modest and must be interpreted relative to target prevalence, a baseline model, and evaluation methodology.

Determine whether model files exist locally.

If trained model artifacts are ignored by Git, they may be absent from the fresh clone.

Locate the actual training and evaluation commands before attempting to retrain.

Do not claim successful model restoration unless inference works.

## 3.5 Frontend

The previous session reported connected pages for:

- Dashboard
- Claims Explorer
- SupplyTrace
- Network Explorer
- Risk Forecasts
- SIU Queue
- Investigation Case Workspace

The frontend reportedly used React and TypeScript.

It also reported:

- Six passing frontend tests
- Frontend dependency audit: zero reported vulnerabilities after updates

Verify the current dependency audit independently when possible.

A passing frontend test suite does not necessarily establish that all screens work interactively in a real browser.

## 3.6 Human review

The previous session reported:

- Finding resolution requires selected evidence and a reason
- Original findings are preserved
- Case priority is recalculated after review
- Investigator actions are recorded
- Audit records persist
- Repeat analysis was tested for stable behavior

Verify these behaviors using actual API calls or browser interactions.

## 3.7 Backend tests

The previous session reported:

- 54 passing Python tests
- Separate test database
- End-to-end review-flow test
- Stable-rerun tests
- Backend integration tests

Run the test suite again on the new environment.

Do not simply repeat the historical passing-test count.

## 3.8 Items NOT confirmed complete

The previous session hit a Codex usage limit while attempting final browser inspection.

The following were NOT conclusively verified:

- Actual browser-based end-to-end walkthrough
- Live Groq API generation using a configured key
- Full Evidence Challenger interaction in the browser
- Final live smoke test
- Complete demo instructions
- Functionality after a fresh Git clone
- Model restoration/retraining on a second computer
- Database reconstruction on a fresh machine

These should receive special attention.

---

# 4. TECHNOLOGY STACK

Expected architecture:

## Frontend

- React
- TypeScript
- Vite
- Tailwind CSS
- shadcn/ui
- TanStack Query
- TanStack Table
- Apache ECharts
- Cytoscape.js
- Lucide React

## Backend

- Python 3.12
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- psycopg

## Database

- PostgreSQL 17
- Local Docker container
- Persistent Docker volume

## Analytics

- pandas
- NumPy
- SciPy
- scikit-learn
- NetworkX
- joblib

## Machine Learning

- IsolationForest
- HistGradientBoostingClassifier
- LogisticRegression benchmark where implemented

## LLM

- Groq API
- Official Groq Python SDK
- Configurable Groq model
- Structured evidence-based outputs
- Deterministic source verification
- Offline fallback

## Infrastructure

- Docker Compose
- FastAPI service
- PostgreSQL service
- Python analytics worker
- React frontend service

This list describes the intended stack.

VERIFY WHAT THE REPOSITORY ACTUALLY USES.

Do not add unnecessary technologies or rewrite functioning components merely to match these expectations.

The complete operational application should run locally.

Groq is the only intended external AI inference dependency.

Do not deploy to Render, Vercel, Neon, Supabase, AWS, Azure, or other cloud infrastructure.

---

# 5. FIRST TASK — COMPLETE REPOSITORY INSPECTION

Before making significant edits:

## Step 1: Confirm workspace

Identify the actual repository path.

Do not assume it is identical to the previous computer's path.

On the original computer, it was:

C:\Users\smray\Documents\Projects\ClaimShield Nexus

On this computer, use the actual cloned repository location.

## Step 2: Inspect Git state

Run read-only checks such as:

git status
git branch --show-current
git remote -v
git log -5 --oneline

Identify:

- Current branch
- Latest commit
- Uncommitted changes
- Relevant project files
- Whether this is the expected repository

Do not reset or discard changes.

Do not automatically pull, push, commit, merge, or switch branches if doing so could overwrite work.

Report conflicts or unexpected differences.

## Step 3: Inspect files

Read:

- README.md
- docker-compose.yml
- .env.example
- .gitignore
- Backend configuration
- Frontend package.json
- Backend dependencies
- Alembic migrations
- Dataset import scripts
- ML training scripts
- Evaluation scripts
- Existing test files
- Any docs/ files
- Existing Codex or agent-specific instructions

Inspect the actual folder structure.

Identify missing or inconsistent references.

## Step 4: Inspect datasets

Verify:

- Which CSVs are present
- Which CSVs are tracked by Git
- Whether evaluation labels are separate
- Row counts
- Required columns
- Relationships and foreign keys
- Metadata availability

Do not modify source datasets unless there is a documented need.

## Step 5: Inspect model files

Check whether the repository includes:

- Isolation Forest model
- 30-day forecast model
- 60-day forecast model
- 90-day forecast model
- Preprocessing artifacts
- Model metadata
- Evaluation reports

If models are absent, inspect whether they can be reproduced using committed scripts and datasets.

Do not fabricate replacement model artifacts.

## Step 6: Inspect Groq integration

Search backend source code for:

- GROQ_API_KEY
- GROQ_MODEL
- Groq client initialization
- Investigation brief generation
- Evidence Challenger implementation
- API routes
- Structured response validation
- Deterministic fallback generation

Confirm whether these features are fully implemented, partially implemented, or absent.

---

# 6. REQUIRED FIRST REPORT

Before significant modifications, produce a report titled:

CLAIMSHIELD NEXUS — PROJECT STATUS AUDIT

Use the following status categories:

COMPLETE — Verified functional through appropriate testing.

IMPLEMENTED / UNVERIFIED — Code exists but functionality has not been demonstrated.

PARTIAL — Some necessary functionality exists.

MISSING — Required functionality was not found.

BLOCKED — Cannot test because of a specific environmental or external dependency.

BROKEN — An actual test or execution failed.

For each component, report:

1. Current status
2. Exact relevant files
3. What the code actually implements
4. Test or command used for verification
5. Actual result
6. Missing requirements
7. Recommended next action

Report on:

- Repository and Git state
- Docker infrastructure
- PostgreSQL migrations
- Data ingestion
- Dataset integrity
- FastAPI backend
- Claim rules
- SupplyTrace
- Isolation Forest
- 30-day forecast
- 60-day forecast
- 90-day forecast
- Nexus Graph backend
- Network visualization
- Evidence fusion
- Case consolidation
- Smart SIU Queue
- Investigator dashboard
- Case workspace
- Groq integration
- Investigation brief
- Evidence Challenger
- Human review
- Audit trails
- Evaluation
- Tests
- Documentation
- Live demonstration readiness

Include a concise completed-versus-pending checklist.

Do not give a made-up completion percentage.

If a completion estimate is provided, label it subjective and explain its basis.

---

# 7. SECOND COMPUTER SETUP

This project is being transferred to another laptop.

The GitHub repository may contain code and synthetic CSVs, but not necessarily all runtime state.

Before launching the application, verify these prerequisites:

- Git is installed
- VS Code is available
- Docker Desktop is installed and running
- Docker Compose works
- Required Docker engine permissions are available
- WSL2 works if required by the Windows installation
- Sufficient disk space exists for images, datasets, and ML artifacts

Prefer running Python and Node inside the provided Docker environment.

Do not require separate host installations unless absolutely necessary.

Check:

docker --version
docker compose version
docker info

If these fail, explain the specific environment problem.

Do not automatically change Windows system settings or permissions.

---

# 8. ENVIRONMENT VARIABLES AND SECRETS

IMPORTANT:

The original .env file is not expected to transfer through Git.

The real Groq key must never be committed.

Inspect .env.example to identify all required settings.

Potential environment variables include:

POSTGRES_PASSWORD
DATABASE_URL
GROQ_API_KEY
GROQ_MODEL
GROQ_TIMEOUT_SECONDS
VITE_API_BASE_URL

Do not assume all of these exist or are required.

Use the actual source code and Docker Compose configuration to identify the necessary variables.

Create a local .env from .env.example if needed, preserving required existing configuration.

For Groq, expected placeholders may look like:

GROQ_API_KEY=replace_with_your_key
GROQ_MODEL=openai/gpt-oss-120b
GROQ_TIMEOUT_SECONDS=45

The model must be configurable.

Verify actual availability of the model when an authenticated Groq test is authorized.

DO NOT:
- Print API keys
- Paste credentials into reports
- Log credentials
- Commit .env
- Expose the key to frontend code
- Send the key to an unauthorized service

The user will enter the Groq key privately.

If Groq integration exists but .env.example lacks its variables, identify this as a configuration defect and propose the minimal correction.

Check that the backend Docker service receives the intended environment variables.

Do not mistake Docker Compose variable interpolation for container environment injection.

---

# 9. DOCKER RECOVERY PROCEDURE

After inspecting the repository and obtaining approval to run setup:

Identify the actual Docker Compose services.

Typical expected services:

- db
- api
- worker
- frontend

Names may differ. Verify them.

Use:

docker compose config --services

Then inspect Compose configuration for:

- Docker images
- Build contexts
- Environment variables
- Port mappings
- Health checks
- Database volumes
- Startup dependencies
- Worker commands
- Mounted dataset/model directories

If valid, start the services with the documented commands.

A typical command may be:

docker compose up --build -d

Then:

docker compose ps

Inspect logs if anything fails.

Example:

docker compose logs --tail=100

Use actual service names if inspecting an individual service.

DO NOT run:

docker compose down -v

unless explicitly authorized.

That command can delete persisted database volumes.

Do not automatically remove containers, reset databases, or delete trained artifacts.

---

# 10. DATABASE RESTORATION AND IMPORT

A fresh laptop may have an empty PostgreSQL database.

Check whether it contains data before importing anything.

Verify database migrations.

If tables are empty, locate the actual CSV importer.

Determine:

- Correct import command
- Expected import order
- Whether import is idempotent
- Whether database migrations are required first
- Whether the import creates an audit report

Use the repository's real implementation.

Do not invent import command paths.

After importing, verify row counts and relationships.

The expected source dataset includes approximately 20,000 claims, but use actual imported counts in the report.

Do not expose evaluation/ground_truth.csv through ordinary operational APIs.

Ground-truth labels are for permitted offline evaluation and label construction, not for producing investigative facts.

---

# 11. MACHINE-LEARNING RESTORATION

Check whether trained ML artifacts were transferred through Git.

The project's previous .gitignore excluded common model formats such as:

*.joblib
*.pkl
*.pickle

Therefore, the saved models may not be present on this computer.

If missing:

1. Locate existing training scripts.
2. Check the synthetic historical dataset.
3. Verify feature engineering logic.
4. Verify temporal split implementation.
5. Verify target construction.
6. Run the documented training process.
7. Save model artifacts locally.
8. Test inference using actual provider records.

Required intended models:

MODEL 1:
Isolation Forest anomaly detection.

MODEL 2:
30-day recurrence-risk forecast.

MODEL 3:
60-day recurrence-risk forecast.

MODEL 4:
90-day recurrence-risk forecast.

Avoid target leakage and temporal leakage.

Never train on the test set.

Never replace actual model outputs with hardcoded scores.

Record actual new evaluation results.

Do not assume results exactly match the previous computer.

---

# 12. FEATURE-BY-FEATURE VERIFICATION

Verify the following workflows.

## A. Claim Rules

Confirm working detection for:
- Potential duplicate billing
- Impossible timing
- Excessive utilization
- Upcoding indicators where supported
- Unbundling indicators where supported
- Phantom-service/documentation inconsistencies

Check source evidence and rule versions.

A missing encounter is not proof of fraud.

## B. SupplyTrace

Confirm working analysis for:
- Consumable quantities
- Unusual unit prices
- Duplicate supply charges
- Peer comparisons
- Estimate-to-final bill differences where records exist
- Potential bundling conflicts
- Missing-data explanations

Verify the UI displays actual backend evidence.

## C. Anomaly Detection

Confirm:
- Trained Isolation Forest is loaded
- Features are constructed correctly
- Inference runs
- Anomaly scores are displayed
- Scores are not misrepresented as fraud probabilities

## D. Nexus Graph

Confirm:
- NetworkX loads actual relationships
- Graph nodes correspond to existing records
- Edges have source records
- Provider/facility/referral relationships are traceable
- Cytoscape.js renders the graph
- Node and edge selection work
- No fabricated graph relationships

## E. Future Risk

Confirm:
- 30-day prediction works
- 60-day prediction works
- 90-day prediction works
- Evaluation results are available
- Predictions use historical information only
- Limitations are displayed

## F. Evidence Fusion

Confirm:
- Multiple findings can be linked
- Source evidence is retained
- Related findings consolidate into cases
- Reanalysis does not create duplicate cases
- Potential exposure is not double-counted

## G. Smart SIU Queue

Confirm:
- Cases are ranked
- Ranking factors are explained
- Capacity selection works
- Case filters work
- Case assignments work where implemented
- Resolved findings can affect priority
- Prepayment/postpayment state is represented correctly

## H. Investigator Console

Confirm:
- Dashboard counts originate from backend data
- Claims Explorer works
- SupplyTrace screen works
- Network Explorer works
- Forecast screen works
- SIU Queue works
- Case Workspace works
- Loading and error states work

## I. Groq Investigation Brief

Confirm:
- Groq client exists
- Backend receives configuration
- Retrieved evidence is structured
- Brief-generation endpoint exists
- Generated evidence IDs are validated
- No fabricated source references are accepted
- Offline fallback works

A mocked Groq test is not equivalent to a successful live API request.

## J. Evidence Challenger

Confirm:
- Challenger examines existing evidence
- It can suggest alternative explanations
- It identifies missing documentation
- It cannot directly overturn findings
- Investigator verification is required
- Resolution preserves original records
- Priority recalculation works
- Audit events persist

## K. Human Review

Confirm:
- Investigator can open a case
- Investigator can select evidence
- Reviewer reason is recorded
- Finding can be resolved when permitted
- Case can be escalated where implemented
- Original evidence is preserved
- Audit history records actions

---

# 13. AUTOMATED TESTS

Discover the actual commands used by the repository.

Do not assume generic commands will work without inspecting the configuration.

Run available tests in an isolated environment.

Verify:

BACKEND:
- Python unit tests
- API integration tests
- Database tests
- Dataset validation tests
- Model tests
- Case review tests
- Audit tests

FRONTEND:
- TypeScript/build validation
- Vitest tests
- UI component tests
- Dependency audit where practical

INTEGRATION:
- Dataset import
- Analysis execution
- Findings generation
- Case consolidation
- Queue ranking
- Case retrieval
- Evidence review
- Priority recalculation
- Audit persistence

LLM:
- Mocked success
- Invalid response
- Missing key
- Timeout
- Offline fallback
- Invented evidence reference rejection

Report exact test totals and failures.

Never report historical tests as though they were executed in the current session.

---

# 14. LIVE WEBSITE CHECK

This stage was not confirmed complete in the original session.

When services are running, check the actual local application.

Expected addresses:

Frontend:
http://localhost:5173

FastAPI:
http://localhost:8000

API Documentation:
http://localhost:8000/docs

These are expected addresses; confirm actual port mappings.

A successful build is not sufficient.

Verify actual website behavior.

The desired end-to-end demonstration is:

1. Dashboard shows imported data.
2. Open Claims Explorer.
3. Select a claim with traceable findings.
4. View its itemized billing in SupplyTrace.
5. Open relevant provider-network relationships.
6. Inspect a provider's forecast.
7. Open the Smart SIU Queue.
8. Select a consolidated investigation case.
9. Review supporting evidence.
10. Generate an investigation brief using Groq if configured.
11. Run Evidence Challenger.
12. Verify supporting evidence.
13. Resolve an eligible finding.
14. Confirm case priority changes appropriately.
15. Confirm a new audit entry exists.

Identify specific case IDs that are suitable for the demo.

Do not use fake hardcoded demonstrations.

If browser automation is not available, document the limitation and provide an exact manual testing checklist.

---

# 15. GROQ VERIFICATION

Groq integration is a priority because the previous session did not confirm a successful live request.

Check whether the following are implemented:

- Actual Groq SDK import
- Groq client configuration
- Appropriate supported model ID
- Investigation brief endpoint
- Evidence Challenger endpoint
- Structured response handling
- Timeouts and retries
- Source reference validation
- Offline fallback

If a key is unavailable:
- Do not invent one.
- Run non-network tests.
- Report the missing configuration.
- Explain how the user should provide it safely.

If the user has configured a key:
- Verify the configuration without displaying its value.
- Request approval before any live test that could consume billable API usage.
- Make one bounded live test.
- Verify evidence references and output quality.
- Report actual success or failure.

If the external service fails, core claim analysis and SIU functions must still work.

---

# 16. EVALUATION AND SCIENTIFIC LIMITATIONS

This project uses synthetic healthcare data.

Model performance on synthetic data does not establish performance on real healthcare claims.

Evaluate:
- Precision@K
- Recall@K
- False-positive workload
- Financial exposure coverage
- Evidence coverage
- Case consolidation
- Forecast PR-AUC
- Calibration
- Processing time

Compare:
- Rules only
- Rules + anomaly detection
- Rules + anomaly detection + graph + SupplyTrace
- Combined capacity-aware investigation ranking

Do not manufacture results.

Flag weak or missing evaluation.

For the historical PR-AUC values, inspect event prevalence and benchmark models before assessing their quality.

Do not claim that suspicious billing patterns equal confirmed fraud.

Do not claim clinical validation, regulatory certification, or production readiness.

---

# 17. WHAT MUST NOT HAPPEN

DO NOT:

1. Rebuild the entire repository from scratch.
2. Overwrite working files without inspection.
3. Delete existing datasets.
4. Reset or delete PostgreSQL volumes.
5. Reinitialize Git unnecessarily.
6. Force-push to GitHub.
7. Commit secrets.
8. Publish the Groq API key.
9. Deploy to external hosting.
10. Replace real analysis with hardcoded mock results.
11. Fabricate passing tests.
12. Fabricate model performance.
13. Alter hidden evaluation labels to inflate scores.
14. Treat a provider relationship as proof of collusion.
15. Present statistical anomalies as confirmed fraud.
16. Automatically close cases using the LLM.
17. Introduce unnecessary frameworks.
18. Claim that an implementation is finished without checking it.

Preserve the established architecture unless a real technical problem justifies changing it.

---

# 18. HOW TO PRIORITIZE REMAINING WORK

After the status audit, organize remaining work into three categories.

## P0 — Must fix before demonstration

Examples:
- Application cannot start
- PostgreSQL not populated
- API cannot serve data
- Required ML models unavailable
- Analysis cannot create cases
- Dashboard fails
- SIU queue broken
- Case workspace broken
- Human review cannot persist
- Groq integration falsely claims success
- Major source-evidence errors

## P1 — High-value improvements

Examples:
- Fix Groq live integration
- Improve Evidence Challenger usability
- Improve network interactions
- Improve error handling
- Improve workflow navigation
- Add missing integration tests
- Improve weak forecast reporting
- Add reproducible demo instructions

## P2 — Optional enhancements

Examples:
- Animated graph history
- More sophisticated anomaly algorithms
- Additional UI animations
- Extra dashboard charts
- Experimental graph-learning models
- Nonessential advanced features

The priority is a reliable working end-to-end product, not maximum feature count.

---

# 19. REPORT FORMAT REQUIRED FROM AI

At the end of the audit, present the following report.

# CLAIMSHIELD NEXUS — CURRENT STATUS

## A. Repository Information

- Current path:
- Current branch:
- Latest commit:
- Working tree status:
- Source files discovered:
- Missing expected assets:

## B. Environment

- Docker:
- PostgreSQL:
- API:
- Worker:
- Frontend:
- Groq configuration:
- Model artifacts:

## C. Data Status

- CSV datasets found:
- Dataset integrity result:
- Imported claims:
- Imported providers:
- Imported facilities:
- Imported line items:
- Imported supplies:
- Ground-truth isolation:

## D. Feature Completion Matrix

For each feature:

Feature | Status | Verification | Remaining work

Include all intelligence engines, UI pages, case workflows, and Groq features.

## E. Machine-Learning Status

For each model report:

- Artifact present?
- Training script present?
- Training executed?
- Inference working?
- Evaluation results?
- Known limitations?

## F. Test Results

- Backend tests passed:
- Backend tests failed:
- Frontend tests passed:
- Frontend tests failed:
- Integration tests:
- Browser tests:
- Groq live test:
- Tests not executed and reasons:

## G. Confirmed Working Features

Only include actually verified functionality.

## H. Missing or Broken Features

Explain exact defects and affected files.

## I. Deployment/Setup Status

This is a local-only project.

Report whether the application can be restored and run from a fresh clone using documented instructions.

## J. Remaining Work

Provide a prioritized checklist:

P0:
P1:
P2:

For each task:
- What must be done
- Why it matters
- Which files/modules are involved
- What test proves completion
- Estimated effort, clearly labeled as an estimate

## K. Demo Readiness

- Can a user open the dashboard?
- Are real findings visible?
- Can an SIU case be opened?
- Does Groq work?
- Can an investigator resolve a finding?
- Does recalculation work?
- Does the audit trail work?
- Are there any demo-blocking issues?

## L. Next Recommended Action

Identify the single most valuable next implementation step.

Do not make unsupported claims.

---

# 20. WHEN AUTHORIZED TO CONTINUE CODING

After finishing the audit, ask whether to:

A. Fix setup and environment problems
B. Finish missing features
C. Fix failing tests
D. Complete Groq integration
E. Prepare the final hackathon demo
F. Perform a full final verification

Once the user approves a direction, implement the relevant work.

Use small, verifiable milestones.

Run tests after meaningful changes.

Preserve working features.

Update project documentation when behavior changes.

Do not automatically push or deploy code.

If you cannot complete something, state exactly what remains.

---

# 21. FINAL PROJECT ACCEPTANCE CRITERIA

The project is considered complete only when:

[ ] Docker services run successfully on the current computer.
[ ] Database schema and migrations work.
[ ] Synthetic datasets are imported.
[ ] Source relationships remain valid.
[ ] FastAPI serves real records.
[ ] React displays real backend data.
[ ] Claim rules generate traceable findings.
[ ] SupplyTrace analyzes itemized billing.
[ ] Isolation Forest inference works.
[ ] Nexus Graph uses real dataset relationships.
[ ] 30-day forecast works or reports a justified limitation.
[ ] 60-day forecast works or reports a justified limitation.
[ ] 90-day forecast works or reports a justified limitation.
[ ] Findings consolidate into cases.
[ ] SIU queue ranks cases correctly.
[ ] Investigator workspace displays evidence.
[ ] Groq brief generation works or clearly indicates fallback mode.
[ ] LLM evidence references are validated.
[ ] Evidence Challenger supports human verification.
[ ] Human review decisions persist.
[ ] Priority recalculation works.
[ ] Audit history records review actions.
[ ] Automated tests pass.
[ ] Actual browser workflow has been checked.
[ ] A reproducible demonstration is documented.
[ ] No real API keys are committed.
[ ] The project can be restored on another computer.

Do not mark items complete without evidence.

---

# 22. MAIN OBJECTIVE

Your immediate responsibility is NOT to generate more code.

Your immediate responsibility is to understand the state of ClaimShield Nexus after the project has been transferred to a different laptop.

Inspect everything.

Identify exactly what is completed.

Identify exactly what is unfinished.

Verify what works.

Report what is broken.

Recover missing local setup and model artifacts safely.

Then recommend the shortest path to a complete, functional, demonstrable ClaimShield Nexus system.

The user wants an accurate technical handoff, not reassurance or inflated progress claims.

BEGIN WITH THE REPOSITORY AUDIT.
