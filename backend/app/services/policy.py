"""Policy-governed investigation decisions (internal demo policy CLAIMSHIELD-SIU-V1).

Evaluates which investigator actions a case permits, from the case's actual findings, reviewed evidence outcomes and
recorded decisions. The SIU priority score is deliberately NOT an input: a high score never authorizes anything.
These are internal demonstration rules, not CMS, HIPAA or any other official requirement.

Approvals need a trustworthy, authenticated supervisor. This deployment has only a labeled demo identity, so
high-impact recommendations stay PENDING_APPROVAL and the approval endpoint refuses (it never trusts a typed name).
"""
from uuid import uuid4
from sqlalchemy import select, func
from app.core import clean, now
from app.models import cases, findings, case_findings, evidence_requests, decisions
from app.services.cases import ACTIVE, record_action

POLICY_ID = 'CLAIMSHIELD-SIU-V1'
OPEN_CASE = lambda status: status not in {'CLOSED', 'RESOLVED'}
INVESTIGATION_STATES = {'UNDER_REVIEW', 'AWAITING_EVIDENCE', 'ESCALATED'}
# Demo policy: before an external referral, an active finding of this type needs a reviewed outcome of this evidence type.
MANDATORY_EVIDENCE = {'phoenix_successor': 'ownership_acquisition_records', 'stolen_id_batch': 'member_confirmation'}
MIN_INDEPENDENT_SUPPORT = 2
INFERRED_ENGINES = {'ml', 'phoenix'}
ACTIONS = {
    'request_more_evidence': 'Request More Evidence',
    'monitor_provider': 'Monitor Provider',
    'full_investigation': 'Recommend Full Investigation',
    'external_referral': 'Recommend External Referral',
}
APPROVAL_UNAVAILABLE = ('Approval requires an authenticated supervisor. This deployment uses a demo identity without '
                        'authentication or roles, so approvals are disabled and the recommendation stays pending.')

def evaluate(case, fs, requests, recorded):
    """Pure policy evaluation. case: dict with case_status; fs: case findings (finding_id, finding_type, engine,
    status); requests: evidence requests (request_id, finding_id, evidence_type, status); recorded: earlier decisions
    (action, status). Returns per-action status, reasons and missing requirements."""
    open_case = OPEN_CASE(case['case_status'])
    active = [f for f in fs if f['status'] in ACTIVE]
    explained = [f for f in fs if f['status'] == 'EXPLAINED']
    by_id = {f['finding_id']: f for f in fs}
    supports = [r for r in requests if r['status'] == 'VERIFIED_SUPPORTS' and r['finding_id'] in by_id and by_id[r['finding_id']]['status'] in ACTIVE]
    supported = {r['finding_id'] for r in supports} | {f['finding_id'] for f in active if f['status'] == 'ESCALATED'}
    support_refs = sorted({r['request_id'] for r in supports} | {fid for fid in supported if not any(r['finding_id'] == fid for r in supports)})
    unreviewed_inferred = [f for f in active if f['engine'] in INFERRED_ENGINES and f['finding_id'] not in supported]
    reviewed_types = {(r['finding_id'], r['evidence_type']) for r in requests if r['status'] in {'VERIFIED_SUPPORTS', 'VERIFIED_EXPLAINS'}}
    mandatory_missing = sorted({f'{f["finding_type"].replace("_", " ")}: reviewed {MANDATORY_EVIDENCE[f["finding_type"]].replace("_", " ")}' for f in active if f['finding_type'] in MANDATORY_EVIDENCE and (f['finding_id'], MANDATORY_EVIDENCE[f['finding_type']]) not in reviewed_types})
    investigation = case['case_status'] in INVESTIGATION_STATES or any(d['action'] == 'full_investigation' and d['status'] in {'RECOMMENDED', 'APPROVED'} for d in recorded)
    excluded = f' {len(explained)} explained finding(s) are excluded and cannot justify escalation.' if explained else ''
    out = {}
    def result(action, status, reason, missing=(), refs=()):
        out[action] = {'action': action, 'label': ACTIONS[action], 'status': status, 'reason': reason, 'missing': list(missing), 'evidence_refs': list(refs)}
    closed = f'Case is {case["case_status"].lower()}; policy actions apply only to open cases.'
    # Rule 1 — additional evidence.
    if not open_case: result('request_more_evidence', 'BLOCKED', closed)
    elif not active: result('request_more_evidence', 'BLOCKED', 'No active finding to request evidence for.' + excluded)
    else: result('request_more_evidence', 'ALLOWED', f'Open case with {len(active)} active finding(s); requesting documentation needs no fraud determination.')
    # Rule 2 — monitoring.
    if not open_case: result('monitor_provider', 'BLOCKED', closed)
    else: result('monitor_provider', 'ALLOWED', 'Monitoring an open case is permitted without a fraud determination.')
    # Rules 3 and 5 — full investigation needs an active finding with investigator-reviewed supporting evidence.
    if not open_case: result('full_investigation', 'BLOCKED', closed)
    elif not active: result('full_investigation', 'BLOCKED', 'No active finding.' + excluded)
    elif not supported:
        note = f' {len(unreviewed_inferred)} unreviewed ML/Phoenix finding(s) do not count on their own.' if unreviewed_inferred else ''
        result('full_investigation', 'NEEDS_EVIDENCE', 'No active finding is supported by investigator-reviewed evidence yet.' + note + excluded, ['Reviewed evidence supporting at least one active finding'])
    else: result('full_investigation', 'ALLOWED', f'{len(supported)} active finding(s) supported by reviewed evidence.' + excluded, refs=support_refs)
    # Rule 4 — external referral: every requirement plus authorized approval; never ALLOWED outright.
    if not open_case: result('external_referral', 'BLOCKED', closed)
    elif not active: result('external_referral', 'BLOCKED', 'No active finding.' + excluded)
    else:
        missing = []
        if not investigation: missing.append('An active investigation (case under review, or a recorded full-investigation recommendation)')
        if len(support_refs) < MIN_INDEPENDENT_SUPPORT: missing.append(f'At least {MIN_INDEPENDENT_SUPPORT} independently reviewed supporting evidence records (have {len(support_refs)})')
        missing += [f'Mandatory evidence — {m}' for m in mandatory_missing]
        if missing: result('external_referral', 'NEEDS_EVIDENCE', 'Requirements for an external referral recommendation are not met.' + excluded, missing, support_refs)
        else: result('external_referral', 'REQUIRES_APPROVAL', 'Evidence conditions are met; a documented justification and a second, authorized approver are required.', ['Investigator justification (on submission)', 'Approval by an authorized supervisor'], support_refs)
    return out

def readiness(evaluation, case, recorded):
    """Decision readiness for the SIU queue; independent of priority."""
    if not OPEN_CASE(case['case_status']): return 'NO_ACTION_ELIGIBLE'
    if any(d['status'] == 'PENDING_APPROVAL' for d in recorded): return 'APPROVAL_REQUIRED'
    if recorded and recorded[0]['action'] == 'monitor_provider' and recorded[0]['status'] == 'RECOMMENDED': return 'MONITORING'
    if evaluation['external_referral']['status'] == 'REQUIRES_APPROVAL': return 'APPROVAL_REQUIRED'
    if evaluation['full_investigation']['status'] == 'ALLOWED': return 'INVESTIGATION_REVIEW_ELIGIBLE'
    if evaluation['request_more_evidence']['status'] == 'ALLOWED': return 'MORE_EVIDENCE_NEEDED'
    return 'NO_ACTION_ELIGIBLE'

def load(con, case_ids):
    """Policy inputs for several cases with four bulk queries."""
    rows = {r['case_id']: dict(r) for r in con.execute(select(cases).where(cases.c.case_id.in_(case_ids))).mappings()}
    fs, reqs, recs = {k: [] for k in rows}, {k: [] for k in rows}, {k: [] for k in rows}
    for r in con.execute(select(case_findings.c.case_id, findings.c.finding_id, findings.c.finding_type, findings.c.engine, findings.c.status).join(findings, findings.c.finding_id == case_findings.c.finding_id).where(case_findings.c.case_id.in_(case_ids))).mappings(): fs[r['case_id']].append(dict(r))
    for r in con.execute(select(evidence_requests.c.case_id, evidence_requests.c.request_id, evidence_requests.c.finding_id, evidence_requests.c.evidence_type, evidence_requests.c.status).where(evidence_requests.c.case_id.in_(case_ids))).mappings(): reqs[r['case_id']].append(dict(r))
    for r in con.execute(select(decisions).where(decisions.c.case_id.in_(case_ids)).order_by(decisions.c.created_at.desc())).mappings(): recs[r['case_id']].append(dict(r))
    return rows, fs, reqs, recs

def case_policy(con, case_id):
    rows, fs, reqs, recs = load(con, [case_id])
    if case_id not in rows: raise LookupError('Case not found')
    case = rows[case_id]; ev = evaluate(case, fs[case_id], reqs[case_id], recs[case_id])
    reviewed = sum(r['status'].startswith('VERIFIED') for r in reqs[case_id]); open_requests = sum(r['status'] in {'REQUESTED', 'RECEIVED'} for r in reqs[case_id])
    return clean({'policy_id': POLICY_ID, 'case': {'case_id': case_id, 'provider_id': case['primary_entity'], 'case_status': case['case_status'], 'priority_score': case['priority_score'], 'potential_financial_exposure': case['potential_financial_exposure'],
                  'active_findings': sum(f['status'] in ACTIVE for f in fs[case_id]), 'explained_findings': sum(f['status'] == 'EXPLAINED' for f in fs[case_id]), 'evidence_review': {'reviewed': reviewed, 'open_requests': open_requests}},
                  'actions': [ev[a] for a in ACTIONS], 'readiness': readiness(ev, case, recs[case_id]), 'decisions': recs[case_id], 'approval_available': False, 'approval_note': APPROVAL_UNAVAILABLE,
                  'note': 'Internal demonstration policy, not an official regulatory requirement. The SIU priority score is not a policy input.'})

def readiness_many(con, case_ids):
    if not case_ids: return {}
    rows, fs, reqs, recs = load(con, case_ids)
    return {cid: readiness(evaluate(rows[cid], fs[cid], reqs[cid], recs[cid]), rows[cid], recs[cid]) for cid in rows}

def submit(con, case_id, action, justification, actor):
    """Records an investigator recommendation. The backend re-evaluates the policy; nothing external ever happens."""
    if action not in ACTIONS: raise ValueError(f'Unknown action {action}')
    if len((justification or '').strip()) < 10: raise ValueError('A justification of at least ten characters is required')
    if not con.execute(select(cases.c.case_id).where(cases.c.case_id == case_id).with_for_update()).scalar(): raise LookupError('Case not found')
    result = case_policy(con, case_id); ev = next(a for a in result['actions'] if a['action'] == action)
    status = {'ALLOWED': 'RECOMMENDED', 'REQUIRES_APPROVAL': 'PENDING_APPROVAL'}.get(ev['status'], 'BLOCKED')
    row = dict(decision_id='PD-' + uuid4().hex[:16], case_id=case_id, action=action, policy_version=POLICY_ID, evaluation=clean(ev), evidence_refs=ev['evidence_refs'], justification=justification.strip(), requested_by=actor, approved_by=None, status=status)
    con.execute(decisions.insert().values(**row))
    state = {'decision_id': row['decision_id'], 'action': action, 'policy_version': POLICY_ID, 'policy_status': ev['status'], 'decision_status': status, 'missing': ev['missing']}
    record_action(con, case_id, actor, 'policy_evaluation_completed', f'{ACTIONS[action]} evaluated under {POLICY_ID}: {ev["status"].replace("_", " ").lower()}. {ev["reason"]}', {}, state)
    kind, text = {'RECOMMENDED': ('investigation_action_recommended', f'{ACTIONS[action]} recommended. Justification: {row["justification"]}'),
                  'PENDING_APPROVAL': ('additional_approval_required', f'{ACTIONS[action]} recorded as pending; an authorized supervisor must approve. Justification: {row["justification"]}'),
                  'BLOCKED': ('action_blocked_by_policy', f'{ACTIONS[action]} blocked by {POLICY_ID}: {ev["reason"]} Missing: {"; ".join(ev["missing"]) or "n/a"}')}[status]
    record_action(con, case_id, actor, kind, text, {}, state)
    return clean({**row, 'created_at': now()})

def approve(con, decision_id, actor, approve=True):
    """Approval/rejection is refused: no authenticated supervisor roles exist (and a requester can never approve their own)."""
    d = con.execute(select(decisions).where(decisions.c.decision_id == decision_id)).mappings().first()
    if not d: raise LookupError('Decision not found')
    if d['status'] != 'PENDING_APPROVAL': raise ValueError(f'Decision is {d["status"]}; only pending decisions can be reviewed')
    if actor.strip().lower() == d['requested_by'].strip().lower(): raise PermissionError('The requesting investigator cannot approve or reject their own recommendation.')
    raise PermissionError(APPROVAL_UNAVAILABLE)
