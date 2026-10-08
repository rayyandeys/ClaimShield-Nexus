# Policy-governed decisions (CLAIMSHIELD-SIU-V1)

An internal **demonstration** policy that tells investigators which actions a case permits, which need more evidence, and which need approval. It is not a CMS or HIPAA requirement. Nothing is ever sent, suspended or referred automatically; the app only records recommendations.

## Where to find it

- **Case Workspace → Compliance & decisions tab** shows:
  - a case summary
  - four action cards, each Allowed / Needs Evidence / Requires Approval / Blocked, with the reason and what's missing
  - a recommendation form
  - decision history
- **Smart SIU queue → Decision readiness column** shows one of: More evidence needed, Investigation review eligible, Approval required, Monitoring, No action eligible. It is independent of priority.

## Rules (`backend/app/services/policy.py`)

The SIU priority score is not an input to any rule. Explained findings never count toward escalation (Rule 5).

| # | Action | Rule |
|---|---|---|
| 1 | Request more evidence | Allowed for an open case with an active finding. |
| 2 | Monitor provider | Allowed for an open case; no fraud determination needed. |
| 3 | Recommend full investigation | Allowed only if an active finding has investigator-reviewed support: a VERIFIED_SUPPORTS evidence outcome, or an ESCALATED status. Unreviewed ML and Phoenix findings never count. |
| 4 | Recommend external referral | Requires all of the following; it is never Allowed outright: |

Rule 4 requirements:
- an active investigation (case under review, or a recorded full-investigation recommendation)
- at least 2 reviewed supporting evidence records
- the mandatory demo evidence: reviewed ownership/acquisition records for an active Phoenix finding, and a reviewed member confirmation for an active member batch
- a justification on submission
- approval by an authorized supervisor

Closed or resolved cases block every action.

## Recommendations and approval

`POST /api/v1/cases/{id}/decisions` re-evaluates the policy on the server and records one of three results:
- **RECOMMENDED:** the action was allowed.
- **PENDING_APPROVAL:** the action requires approval.
- **BLOCKED:** the action needed evidence or was blocked. Disabled buttons are not the enforcement; the server is.

Every submission writes two audit timeline events: `policy_evaluation_completed`, plus one of `investigation_action_recommended`, `additional_approval_required` or `action_blocked_by_policy`.

The app has no authentication, only a demo identity. So `POST /api/v1/decisions/{id}/approve` and `/reject` always refuse with 403. A requester can never approve their own request, and a typed-in name is never trusted as a supervisor. Pending recommendations stay pending until real authentication with roles exists.

Other endpoint: `GET /api/v1/cases/{id}/policy`. The queue (`GET /api/v1/siu/queue`) items also carry `decision_readiness`. Storage is `policy_decisions` (migration 0004, additive).
