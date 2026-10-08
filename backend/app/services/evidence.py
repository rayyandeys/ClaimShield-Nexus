"""Next-Best-Evidence, read-only outcome simulation, evidence-request lifecycle and simulated member confirmations.

Every hypothetical score comes from the production SIU scorer (cases.rank_values) applied to the same inputs used by
cases.recalculate, with outcomes applied by cases.apply_outcome. Simulations run in READ ONLY transactions.
"""
import json
from collections import defaultdict
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, update, func, and_
from sqlalchemy.dialects.postgresql import insert
from app.core import clean, now
from app.models import cases, findings, finding_claims, case_findings, evidence, evidence_requests, confirmations, tables
from app.schemas import ResolutionInput
from app.services.cases import ACTIVE, rank_values, case_inputs, apply_outcome, corroborate, resolve, record_action
from app.services.detection import stable_id

CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'config' / 'evidence_catalog.json').read_text())
OPEN = {'REQUESTED', 'RECEIVED'}
TRANSITIONS = {'REQUESTED': {'RECEIVED', 'VERIFIED_EXPLAINS', 'VERIFIED_SUPPORTS', 'INCONCLUSIVE', 'WITHDRAWN'}, 'RECEIVED': {'VERIFIED_EXPLAINS', 'VERIFIED_SUPPORTS', 'INCONCLUSIVE', 'WITHDRAWN'}}
# Conservative interpretation of simulated member responses: which reviewed outcomes each response permits.
RESPONSE_OUTCOMES = {'YES': {'VERIFIED_EXPLAINS', 'INCONCLUSIVE', 'WITHDRAWN'}, 'NO': {'VERIFIED_SUPPORTS', 'INCONCLUSIVE', 'WITHDRAWN'}, 'NOT_SURE': {'INCONCLUSIVE', 'WITHDRAWN'}}
SCORER = 'app.services.cases.rank_values (policy siu-1.0)'
DECISION_FORMULA = 'decision_value = expected_absolute_priority_shift × (0.3 + 0.7 × probability_of_review_decision_change) / max(estimated_days, 0.5)'
CODE_LABELS = [('SIM-DME-CATHETER', 'medical supply (catheter supplies)'), ('SIM-DME', 'medical equipment or supplies'), ('SIM-VISIT', 'office visit'), ('SIM-CONSULT', 'consultation'), ('SIM-LAB', 'laboratory test'), ('SIM-BH', 'behavioral health service'), ('SIM-HH', 'home health visit'), ('SIM-RX', 'prescription'), ('SIM-AMB', 'ambulance transport'), ('SIM-SURGERY', 'procedure'), ('SIM-IMAGING', 'imaging service')]

class Conflict(Exception):
    """A request conflicts with the current recorded state (HTTP 409)."""

def beta_smoothed(benign, total):
    return (benign + 1) / (total + 2)

def decision_metrics(p, base, clear, confirm, cost_days):
    """Directed delta (spec heuristic), absolute shift and capacity-decision change, kept separate.
    Evidence that would move a case across the capacity boundary is valuable even when the signed delta is negative."""
    delta = p * (base['priority'] - clear['priority']) + (1 - p) * (confirm['priority'] - base['priority'])
    shift = p * abs(base['priority'] - clear['priority']) + (1 - p) * abs(confirm['priority'] - base['priority'])
    change = p * (clear['in_capacity'] != base['in_capacity']) + (1 - p) * (confirm['in_capacity'] != base['in_capacity'])
    cost = max(cost_days, 0.5)
    return {'expected_directed_delta': round(delta, 3), 'expected_absolute_shift': round(shift, 3), 'decision_change_probability': round(change, 4), 'crosses_capacity_boundary': clear['in_capacity'] != base['in_capacity'] or confirm['in_capacity'] != base['in_capacity'], 'legacy_voi': round(max(0.0, delta) * (1.0 if change > 0 else 0.3) / cost, 4), 'decision_value': round(shift * (0.3 + 0.7 * change) / cost, 4)}

def catalog_entry(finding_type, evidence_type):
    return next((e for e in CATALOG['by_finding_type'].get(finding_type, []) if e['evidence_type'] == evidence_type), None)

def probability(con, finding_type, evidence_type, default):
    """Beta(1,1)-smoothed share of reviewed outcomes that explained the finding, else the labeled default."""
    rows = con.execute(select(evidence_requests.c.status, func.count()).join(findings, findings.c.finding_id == evidence_requests.c.finding_id).where(findings.c.finding_type == finding_type, evidence_requests.c.evidence_type == evidence_type, evidence_requests.c.status.in_(['VERIFIED_EXPLAINS', 'VERIFIED_SUPPORTS'])).group_by(evidence_requests.c.status)).all()
    counts = dict(rows); n = sum(counts.values()); benign = counts.get('VERIFIED_EXPLAINS', 0)
    if n >= CATALOG['min_history']: return beta_smoothed(benign, n), 'synthetic_historical_estimate', n
    return default, 'synthetic_default_assumption' if n == 0 else 'insufficient_history', n

def queue_scores(con):
    """Stored priorities of every queue-eligible case (same filter as the SIU queue endpoint)."""
    return [(r.case_id, float(r.priority_score)) for r in con.execute(select(cases.c.case_id, cases.c.priority_score).where(~cases.c.case_status.in_(['CLOSED', 'RESOLVED'])))]

def ranked(scores, case_id, score, case_status):
    """Queue order with one case's score replaced: priority desc, case_id asc; score 0 or closed means not queued."""
    items = [(c, s) for c, s in scores if c != case_id]
    if case_status not in {'CLOSED', 'RESOLVED'}: items.append((case_id, score))
    return [c for c, s in sorted((x for x in items if x[1] > 0), key=lambda x: (-x[1], x[0]))]

def position(order, case_id):
    return order.index(case_id) + 1 if case_id in order else None

def reviewed_denials(con, finding_id):
    return set(con.execute(select(confirmations.c.member_id).join(evidence_requests, evidence_requests.c.request_id == confirmations.c.request_id).where(confirmations.c.finding_id == finding_id, confirmations.c.response == 'NO', evidence_requests.c.status == 'VERIFIED_SUPPORTS')).scalars())

def scenario(case, fs, cs, forecast, scores, finding_id, outcome, capacity, denials_after=0):
    values = rank_values(apply_outcome(fs, finding_id, outcome, denials_after), cs, forecast)
    order = ranked(scores, case['case_id'], values['priority_score'], case['case_status'])
    pos = position(order, case['case_id'])
    return {'priority': values['priority_score'], 'queue_position': pos, 'in_capacity': pos is not None and pos <= capacity, 'top_k': order[:capacity], 'factors': values['ranking']['factors'], 'severity': values['severity'], 'exposure': values['potential_financial_exposure']}

def current(case, fs, cs, forecast, scores, capacity):
    values = rank_values(fs, cs, forecast); order = ranked(scores, case['case_id'], values['priority_score'], case['case_status']); pos = position(order, case['case_id'])
    return {'priority': values['priority_score'], 'stored_priority': case['priority_score'], 'queue_position': pos, 'in_capacity': pos is not None and pos <= capacity, 'top_k': order[:capacity], 'factors': values['ranking']['factors'], 'queue_size': len(order)}

def unconfirmed_claims(con, case_id, finding):
    asked = set(con.execute(select(confirmations.c.claim_id).where(confirmations.c.case_id == case_id)).scalars())
    return [c for c in sorted(finding['claim_ids']) if c not in asked]

def recommendations(con, case_id, capacity=10, limit=3):
    case, fs, cs, forecast = case_inputs(con, case_id)
    if not case: raise LookupError('Case not found')
    scores = queue_scores(con); base = current(case, fs, cs, forecast, scores, capacity)
    open_requests = [dict(r) for r in con.execute(select(evidence_requests).where(evidence_requests.c.case_id == case_id, evidence_requests.c.status.in_(OPEN))).mappings()]
    items, outstanding = [], []
    for f in fs:
        if f['status'] not in ACTIVE: continue
        for entry in CATALOG['by_finding_type'].get(f['finding_type'], []):
            et = entry['evidence_type']; pending = [r for r in open_requests if r['finding_id'] == f['finding_id'] and r['evidence_type'] == et]
            subject = None
            if et == 'member_confirmation':
                remaining = unconfirmed_claims(con, case_id, f)
                if not remaining:
                    if pending: outstanding.append({'finding_id': f['finding_id'], 'evidence_type': et, 'open_requests': len(pending)})
                    continue
                subject = remaining[0]
            elif pending:
                outstanding.append({'finding_id': f['finding_id'], 'evidence_type': et, 'open_requests': len(pending), 'request_ids': [r['request_id'] for r in pending]}); continue
            p, source, n = probability(con, f['finding_type'], et, entry['p_benign'])
            denials = len(reviewed_denials(con, f['finding_id'])) + 1 if et == 'member_confirmation' else 0
            clear = scenario(case, fs, cs, forecast, scores, f['finding_id'], 'explains', capacity)
            confirm = scenario(case, fs, cs, forecast, scores, f['finding_id'], 'supports', capacity, denials)
            meta = CATALOG['evidence_types'][et]
            items.append({'finding_id': f['finding_id'], 'finding_type': f['finding_type'], 'finding_severity': f['severity'], 'evidence_type': et, 'label': meta['label'], 'if_explains': meta['explains'], 'if_supports': meta['supports'], 'subject_claim_id': subject, 'estimated_days': entry['estimated_days'], 'p_benign': round(p, 4), 'probability_source': source, 'history_count': n,
                          'outcomes': {'explains': {k: clear[k] for k in ['priority', 'queue_position', 'in_capacity']}, 'supports': {k: confirm[k] for k in ['priority', 'queue_position', 'in_capacity']}},
                          **decision_metrics(p, base, clear, confirm, entry['estimated_days']), 'open_requests': len(pending)})
    # Deterministic ordering: value, then cheaper, then stable identifiers.
    items.sort(key=lambda x: (-x['decision_value'], x['estimated_days'], x['finding_id'], x['evidence_type']))
    for i, x in enumerate(items): x['rank'] = i + 1
    return clean({'case_id': case_id, 'hypothetical': True, 'capacity': capacity, 'current': {k: base[k] for k in ['priority', 'stored_priority', 'queue_position', 'in_capacity', 'queue_size']}, 'recommendations': items[:limit], 'considered': len(items), 'outstanding': outstanding, 'evidence_version': case['evidence_version'], 'scorer': SCORER, 'formula': DECISION_FORMULA, 'catalog_version': CATALOG['version'], 'probability_note': CATALOG['probability_note'], 'notice': 'Hypothetical estimates. Probabilities are synthetic assumptions or small-sample synthetic estimates, not validated real-world rates.'})

def simulate(con, case_id, finding_id, outcome, capacity=10, evidence_type=None):
    """Read-only what-if: never writes. Callers run it inside a READ ONLY transaction."""
    case, fs, cs, forecast = case_inputs(con, case_id)
    if not case: raise LookupError('Case not found')
    target = next((f for f in fs if f['finding_id'] == finding_id), None)
    if not target: raise ValueError('Finding does not belong to this case')
    if target['status'] not in ACTIVE: raise ValueError('Finding is not active; there is no outcome to simulate')
    if outcome not in {'explains', 'supports'}: raise ValueError('Outcome must be explains or supports')
    scores = queue_scores(con); base = current(case, fs, cs, forecast, scores, capacity)
    denials = len(reviewed_denials(con, finding_id)) + 1 if evidence_type == 'member_confirmation' and outcome == 'supports' else 0
    sim = scenario(case, fs, cs, forecast, scores, finding_id, outcome, capacity, denials)
    entering = [c for c in sim['top_k'] if c not in base['top_k']]; leaving = [c for c in base['top_k'] if c not in sim['top_k']]
    titles = dict(con.execute(select(cases.c.case_id, cases.c.title).where(cases.c.case_id.in_(entering + leaving))).all())
    return clean({'hypothetical': True, 'label': 'HYPOTHETICAL — nothing was saved', 'case_id': case_id, 'finding_id': finding_id, 'outcome': outcome, 'capacity': capacity, 'evidence_version': case['evidence_version'], 'original': {k: base[k] for k in ['priority', 'queue_position', 'in_capacity', 'factors']}, 'simulated': {k: sim[k] for k in ['priority', 'queue_position', 'in_capacity', 'factors', 'severity', 'exposure']}, 'entering_top_k': [{'case_id': c, 'title': titles.get(c)} for c in entering], 'leaving_top_k': [{'case_id': c, 'title': titles.get(c)} for c in leaving], 'scorer': SCORER})

def queue_summary(con, capacity=10, window=10):
    """Best evidence request for each case in the top K plus the next `window` cases (where boundary crossings happen)."""
    order = [c for c, s in sorted((x for x in queue_scores(con) if x[1] > 0), key=lambda x: (-x[1], x[0]))][:capacity + window]
    rows = []
    for cid in order:
        rec = recommendations(con, cid, capacity, limit=1)
        if rec['recommendations']: rows.append({'case_id': cid, 'queue_position': rec['current']['queue_position'], 'priority': rec['current']['priority'], **{k: rec['recommendations'][0][k] for k in ['finding_id', 'finding_type', 'evidence_type', 'label', 'decision_value', 'decision_change_probability', 'crosses_capacity_boundary', 'estimated_days', 'probability_source']}})
    rows.sort(key=lambda r: (-r['decision_value'], r['queue_position'] or 10**6, r['case_id']))
    titles = dict(con.execute(select(cases.c.case_id, cases.c.title).where(cases.c.case_id.in_([r['case_id'] for r in rows]))).all())
    return clean({'hypothetical': True, 'capacity': capacity, 'items': [dict(r, title=titles.get(r['case_id'])) for r in rows], 'formula': DECISION_FORMULA})

def case_finding(con, case_id, finding_id):
    row = con.execute(select(findings).join(case_findings).where(case_findings.c.case_id == case_id, findings.c.finding_id == finding_id)).mappings().first()
    if not row: raise ValueError('Finding does not belong to this case')
    return dict(row)

def lock_case(con, case_id):
    case = con.execute(select(cases).where(cases.c.case_id == case_id).with_for_update()).mappings().first()
    if not case: raise LookupError('Case not found')
    return dict(case)

def create_request(con, case_id, finding_id, evidence_type, actor, justification=None, subject_id=None, capacity=10):
    lock_case(con, case_id); f = case_finding(con, case_id, finding_id)
    if f['status'] not in ACTIVE: raise ValueError('Evidence can only be requested for an active finding')
    entry = catalog_entry(f['finding_type'], evidence_type)
    if not entry: raise ValueError(f'{evidence_type} is not an applicable evidence type for {f["finding_type"]}')
    if evidence_type == 'member_confirmation' and not subject_id: raise ValueError('Member confirmation requests need a claim; generate a confirmation notice for a specific claim')
    duplicate = con.execute(select(evidence_requests).where(evidence_requests.c.case_id == case_id, evidence_requests.c.finding_id == finding_id, evidence_requests.c.evidence_type == evidence_type, evidence_requests.c.status.in_(OPEN), evidence_requests.c.subject_id == subject_id if subject_id else evidence_requests.c.subject_id.is_(None))).mappings().first()
    if duplicate and not (justification or '').strip(): return clean({**dict(duplicate), 'duplicate': True})
    rec = recommendations(con, case_id, capacity, limit=100)
    match = next((r for r in rec['recommendations'] if r['finding_id'] == finding_id and r['evidence_type'] == evidence_type), None)
    p, source, n = probability(con, f['finding_type'], evidence_type, entry['p_benign'])
    snapshot = {'current': rec['current'], 'recommendation': match, 'catalog_version': CATALOG['version'], 'evidence_version': rec['evidence_version'], 'history_count': n, 'scorer': SCORER}
    row = dict(request_id='ER-' + uuid4().hex[:16], case_id=case_id, finding_id=finding_id, evidence_type=evidence_type, subject_id=subject_id, status='REQUESTED', requested_by=actor, estimated_days=entry['estimated_days'], estimated_p_benign=p, probability_source=source, score_snapshot=clean(snapshot), justification=justification)
    con.execute(evidence_requests.insert().values(**row))
    record_action(con, case_id, actor, 'evidence_request_created', f'Requested {CATALOG["evidence_types"][evidence_type]["label"]}' + (f' for claim {subject_id}' if subject_id else '') + (f'. Justification: {justification}' if justification else '.'), {}, {'request_id': row['request_id'], 'evidence_type': evidence_type, 'status': 'REQUESTED'}, finding_id)
    return clean({**row, 'duplicate': False})

def outcome_evidence(con, request, f, status, notes, actor, confirmation=None, target_finding_id=None):
    """A reviewed outcome becomes a source-linked evidence record on the finding; earlier evidence is never modified."""
    finding_id = target_finding_id or f['finding_id']
    evidence_id = stable_id('E-', finding_id, 'evidence_requests', request['request_id'])
    observed = {'request_id': request['request_id'], 'evidence_type': request['evidence_type'], 'reviewed_outcome': status, 'review_notes': notes, 'reviewer': actor}
    if confirmation: observed.update(member_response=confirmation['response'], claim_id=confirmation['claim_id'], member_id=confirmation['member_id'], simulated=confirmation['simulated'])
    con.execute(insert(evidence).values(evidence_id=evidence_id, finding_id=finding_id, source_table_or_type='evidence_requests', source_record_id=request['request_id'], evidence_type=request['evidence_type'], observed_value=clean(observed), reference_value_or_context={'catalog_version': CATALOG['version']}, record_timestamp=now(), provenance={'source': 'investigator_review', 'synthetic': True, 'simulated_member_response': bool(confirmation and confirmation['simulated'])}, verification_status='reviewed_simulated_member_response' if confirmation else 'investigator_reviewed').on_conflict_do_nothing())
    return evidence_id

def member_finding(con, case_id, member_id):
    row = con.execute(select(findings).join(case_findings).where(case_findings.c.case_id == case_id, findings.c.finding_type == 'member_id_possibly_compromised', findings.c.entity_id == member_id, findings.c.status.in_(ACTIVE))).mappings().first()
    return dict(row) if row else None

def record_outcome(con, request_id, status, actor, notes):
    request = con.execute(select(evidence_requests).where(evidence_requests.c.request_id == request_id).with_for_update()).mappings().first()
    if not request: raise LookupError('Evidence request not found')
    request = dict(request)
    if request['status'] == status: return clean({**request, 'idempotent': True})
    if status not in TRANSITIONS.get(request['status'], set()): raise Conflict(f'Request is {request["status"]}; {status} is not allowed')
    if status.startswith('VERIFIED') and len((notes or '').strip()) < 10: raise ValueError('A reviewed outcome needs an explanation of at least ten characters')
    case_id = request['case_id']; lock_case(con, case_id); f = case_finding(con, case_id, request['finding_id'])
    confirmation = None
    if request['evidence_type'] == 'member_confirmation':
        confirmation = con.execute(select(confirmations).where(confirmations.c.request_id == request_id)).mappings().first()
        confirmation = dict(confirmation) if confirmation else None
        if status not in {'WITHDRAWN', 'RECEIVED'}:
            if not confirmation or confirmation['response'] == 'PENDING': raise Conflict('Record the member response before reviewing it')
            if status not in RESPONSE_OUTCOMES[confirmation['response']]: raise ValueError(f'A {confirmation["response"]} response cannot be reviewed as {status}')
    if status.startswith('VERIFIED') and f['status'] not in ACTIVE: raise Conflict('The finding is no longer active; record an inconclusive or withdrawn outcome instead')
    evidence_id = None; effects = []
    if status in {'VERIFIED_EXPLAINS', 'VERIFIED_SUPPORTS', 'INCONCLUSIVE'}:
        evidence_id = outcome_evidence(con, request, f, status, notes, actor, confirmation)
    if status == 'VERIFIED_EXPLAINS':
        if confirmation and f['finding_type'] == 'stolen_id_batch':
            # One member's confirmation explains only that member's allegation, never the whole batch.
            mf = member_finding(con, case_id, confirmation['member_id'])
            if mf: mid = outcome_evidence(con, request, f, status, notes, actor, confirmation, mf['finding_id']); effects.append(resolve(con, mf['finding_id'], ResolutionInput(actor=actor, status='EXPLAINED', explanation=notes, verified_evidence_ids=[mid])))
            record_action(con, case_id, actor, 'member_confirmed_receipt', f'Reviewed simulated member response: member {confirmation["member_id"]} reported receiving the service on claim {confirmation["claim_id"]}. Batch finding unchanged.', {}, {'request_id': request_id}, f['finding_id'])
        else: effects.append(resolve(con, f['finding_id'], ResolutionInput(actor=actor, status='EXPLAINED', explanation=notes, verified_evidence_ids=[evidence_id])))
    elif status == 'VERIFIED_SUPPORTS':
        denials = 0
        if confirmation:
            denials = len(reviewed_denials(con, f['finding_id']) | {confirmation['member_id']})
            mf = member_finding(con, case_id, confirmation['member_id']) if f['finding_type'] == 'stolen_id_batch' else None
            if mf: mid = outcome_evidence(con, request, f, status, notes, actor, confirmation, mf['finding_id']); effects.append(corroborate(con, mf['finding_id'], actor, notes, [mid]))
        effects.append(corroborate(con, f['finding_id'], actor, notes, [evidence_id], denials))
    con.execute(update(evidence_requests).where(evidence_requests.c.request_id == request_id).values(status=status, reviewer=actor, review_notes=notes, evidence_id=evidence_id, outcome_at=now() if status != 'RECEIVED' else None, updated_at=now()))
    label = {'RECEIVED': 'evidence_received_awaiting_review', 'WITHDRAWN': 'evidence_request_withdrawn'}.get(status, 'evidence_outcome_reviewed')
    record_action(con, case_id, actor, label, notes or status.lower().replace('_', ' '), {'status': request['status']}, {'status': status, 'request_id': request_id, 'evidence_id': evidence_id}, f['finding_id'])
    return clean({**request, 'status': status, 'reviewer': actor, 'review_notes': notes, 'evidence_id': evidence_id, 'effects': effects})

def list_requests(con, case_id):
    return clean([dict(r) for r in con.execute(select(evidence_requests).where(evidence_requests.c.case_id == case_id).order_by(evidence_requests.c.created_at.desc())).mappings()])

def service_label(code):
    return next((label for prefix, label in CODE_LABELS if code.startswith(prefix)), 'healthcare service')

def notice_text(claim, provider):
    return (f'SIMULATED NOTICE — ClaimShield prototype. No message was sent to any person.\n\n'
            f'We\'re reviewing a healthcare claim submitted under your member record.\n'
            f'Our records show a {service_label(claim["primary_code"])} claim dated {claim["service_date"]:%B %d, %Y} from {provider["provider_name"]}.\n\n'
            f'Did you receive the item or service listed?\nYes / No / I\'m not sure\n\n'
            f'We will never ask you for passwords, identification documents or banking information.')

def create_confirmation(con, case_id, claim_id, actor, finding_id=None, member_id=None):
    lock_case(con, case_id)
    claim = con.execute(select(tables['claims']).where(tables['claims'].c.claim_id == claim_id)).mappings().first()
    if not claim: raise LookupError('Claim not found')
    if member_id and member_id != claim['member_id']: raise ValueError('That member is not the member on this claim')
    linked = [dict(r) for r in con.execute(select(findings).join(case_findings).join(finding_claims, finding_claims.c.finding_id == findings.c.finding_id).where(case_findings.c.case_id == case_id, finding_claims.c.claim_id == claim_id, findings.c.status.in_(ACTIVE))).mappings()]
    eligible = [f for f in linked if catalog_entry(f['finding_type'], 'member_confirmation')]
    if finding_id: eligible = [f for f in eligible if f['finding_id'] == finding_id]
    if not eligible: raise ValueError('No active finding in this case on this claim accepts a member confirmation')
    preference = {'stolen_id_batch': 0, 'member_id_possibly_compromised': 1, 'missing_encounter_indicator': 2}
    f = sorted(eligible, key=lambda f: (preference.get(f['finding_type'], 9), f['finding_id']))[0]
    existing = con.execute(select(confirmations).where(confirmations.c.case_id == case_id, confirmations.c.claim_id == claim_id)).mappings().first()
    if existing: return clean({**dict(existing), 'duplicate': True})
    request = create_request(con, case_id, f['finding_id'], 'member_confirmation', actor, subject_id=claim_id)
    provider = con.execute(select(tables['providers']).where(tables['providers'].c.provider_id == claim['provider_id'])).mappings().one()
    row = dict(confirmation_id='MC-' + uuid4().hex[:16], case_id=case_id, finding_id=f['finding_id'], claim_id=claim_id, member_id=claim['member_id'], request_id=request['request_id'], notice_text=notice_text(claim, provider), created_by=actor, simulated=True, response='PENDING', source='simulated_prototype_workflow')
    con.execute(confirmations.insert().values(**row))
    record_action(con, case_id, actor, 'simulated_member_notice_generated', f'Simulated confirmation notice generated for claim {claim_id}. No member was contacted.', {}, {'confirmation_id': row['confirmation_id'], 'request_id': request['request_id']}, f['finding_id'])
    return clean({**row, 'duplicate': False})

def record_response(con, confirmation_id, response, actor, notes=None):
    row = con.execute(select(confirmations).where(confirmations.c.confirmation_id == confirmation_id).with_for_update()).mappings().first()
    if not row: raise LookupError('Confirmation not found')
    row = dict(row)
    if row['response'] == response: return clean({**row, 'idempotent': True})
    if row['response'] != 'PENDING': raise Conflict(f'A {row["response"]} response is already recorded; responses are not overwritten')
    con.execute(update(confirmations).where(confirmations.c.confirmation_id == confirmation_id).values(response=response, response_at=now(), notes=notes))
    con.execute(update(evidence_requests).where(evidence_requests.c.request_id == row['request_id'], evidence_requests.c.status == 'REQUESTED').values(status='RECEIVED', updated_at=now()))
    record_action(con, row['case_id'], actor, 'simulated_member_response_recorded', f'Simulated member response "{response}" recorded for claim {row["claim_id"]}; awaiting investigator review.' + (f' Notes: {notes}' if notes else ''), {'response': 'PENDING'}, {'response': response, 'confirmation_id': confirmation_id}, row['finding_id'])
    return clean({**row, 'response': response, 'idempotent': False})

def list_confirmations(con, case_id):
    rows = con.execute(select(confirmations, evidence_requests.c.status.label('request_status')).join(evidence_requests, evidence_requests.c.request_id == confirmations.c.request_id).where(confirmations.c.case_id == case_id).order_by(confirmations.c.created_at.desc())).mappings()
    return clean([dict(r) for r in rows])

def spillover(con, case_id, limit=100):
    """Other claims of members in this case's member-batch findings. Leads only: never added to case exposure."""
    member_findings = list(con.execute(select(findings.c.finding_id, findings.c.status).join(case_findings).where(case_findings.c.case_id == case_id, findings.c.finding_type.in_(['stolen_id_batch', 'member_id_possibly_compromised']))).all())
    if not member_findings: return {'case_id': case_id, 'applicable': False, 'reason': 'This case has no member-batch findings.', 'groups': [], 'claims': [], 'totals': {}}
    claims = tables['claims']; ids = [f.finding_id for f in member_findings]
    case_claims = select(finding_claims.c.claim_id).join(case_findings, case_findings.c.finding_id == finding_claims.c.finding_id).where(case_findings.c.case_id == case_id)
    members = select(claims.c.member_id).where(claims.c.claim_id.in_(select(finding_claims.c.claim_id).where(finding_claims.c.finding_id.in_(ids)))).distinct()
    rows = [dict(r) for r in con.execute(select(claims).where(claims.c.member_id.in_(members), ~claims.c.claim_id.in_(case_claims)).order_by(claims.c.service_date.desc(), claims.c.claim_id)).mappings()]
    groups = defaultdict(lambda: {'claims': 0, 'billed': 0.0, 'allowed': 0.0, 'paid': 0.0, 'members': set()})
    for c in rows:
        g = groups[(c['provider_id'], c['claim_type'], f'{c["service_date"]:%Y-%m}')]; g['claims'] += 1; g['members'].add(c['member_id'])
        for k, f in [('billed', 'billed_amount_usd'), ('allowed', 'allowed_amount_usd'), ('paid', 'paid_amount_usd')]: g[k] += float(c[f])
    corroborated = any(f.status == 'ESCALATED' for f in member_findings)
    totals = {'claims': len(rows), 'members': len({c['member_id'] for c in rows}), 'billed': round(sum(float(c['billed_amount_usd']) for c in rows), 2), 'allowed': round(sum(float(c['allowed_amount_usd']) for c in rows), 2), 'paid': round(sum(float(c['paid_amount_usd']) for c in rows), 2)}
    out = [{'provider_id': k[0], 'claim_type': k[1], 'month': k[2], **{x: (round(v, 2) if isinstance(v, float) else len(v) if isinstance(v, set) else v) for x, v in g.items()}} for k, g in sorted(groups.items(), key=lambda kv: (-kv[1]['paid'], kv[0]))]
    return clean({'case_id': case_id, 'applicable': True, 'corroborated': corroborated, 'groups': out, 'claims': rows[:limit], 'totals': totals, 'note': 'Investigation leads only. Spillover claims are not confirmed fraud, are not added to case exposure and never block payment. Each claim is counted once.'})
