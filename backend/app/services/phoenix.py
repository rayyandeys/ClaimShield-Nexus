"""Phoenix Provider Detector: possible successors of suspended, revoked or closed providers.

A flagged link means the pair shares enough evidence to investigate an operational connection. It never transfers
the predecessor's findings to the successor; the successor's finding cites only the successor's own records and
the comparison evidence.
"""
import json
import math
from collections import Counter, defaultdict
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path
from app.services.detection import make_finding, stable_id

CONFIG = json.loads((Path(__file__).resolve().parent.parent / 'config' / 'detectors.json').read_text())['phoenix']
COMPONENTS = ['patient_overlap', 'shared_identifiers', 'billing_similarity', 'referral_overlap', 'timing']

def jaccard(a, b):
    """None when both sets are empty (never treated as identical)."""
    return None if not a and not b else len(a & b) / len(a | b)

def cosine(a, b):
    if not a or not b: return None
    dot = sum(a[k] * b.get(k, 0) for k in a)
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))

def normalize_address(text):
    return ' '.join(''.join(ch for ch in (text or '').lower() if ch.isalnum() or ch == ' ').split())

def address_similarity(x, y):
    """Fuzzy street/city similarity, but house numbers must match exactly (neighbors on one street are not one address)."""
    a, b = normalize_address(x).split(), normalize_address(y).split()
    number_a = a[0] if a and a[0].isdigit() else None; number_b = b[0] if b and b[0].isdigit() else None
    if number_a != number_b: return 0.0
    return SequenceMatcher(None, ' '.join(a), ' '.join(b)).ratio()

def identifier_matches(a, b):
    """Exact matches for stable identifiers, fuzzy for addresses. Missing values are 'not compared', never matches."""
    matches, compared = {}, []
    for field, weight in CONFIG['identifier_weights'].items():
        x, y = a.get(field), b.get(field)
        if not x or not y: continue
        compared.append(field)
        if field == 'address':
            similarity = address_similarity(x, y)
            if similarity >= CONFIG['address_similarity_threshold']: matches[field] = {'weight': weight, 'similarity': round(similarity, 3)}
        elif x == y: matches[field] = {'weight': weight}
    return round(sum(m['weight'] for m in matches.values()), 4), matches, compared

def eligibility(data, provider_id):
    """Why a provider can or cannot take part in a predecessor/successor comparison (documented stream contract)."""
    profiles = {p['provider_id']: p for p in data.get('provider_profiles', [])}
    p = profiles.get(provider_id)
    if not p: return {'eligible': False, 'reason': 'No provider profile (enrollment/status record); Phoenix cannot compare this provider.'}
    roles = []
    if p['operating_status'] in CONFIG['inactive_statuses'] and p.get('status_effective_date'): roles.append('predecessor')
    if p.get('enrollment_date') and any(q['operating_status'] in CONFIG['inactive_statuses'] and q.get('status_effective_date') and q['status_effective_date'] < p['enrollment_date'] <= q['status_effective_date'] + timedelta(days=CONFIG['eligibility_days']) for pid, q in profiles.items() if pid != provider_id): roles.append('successor')
    if not roles: return {'eligible': False, 'reason': f'No suspended, revoked or closed provider changed status within {CONFIG["eligibility_days"]} days before this provider enrolled, and this provider is active.'}
    return {'eligible': True, 'roles': roles}

def score_pair(components):
    total = sum(CONFIG['weights'][k] * (components[k] or 0.0) for k in COMPONENTS)
    supporting = [k for k in COMPONENTS if k != 'timing' and (components[k] or 0.0) >= CONFIG['support_threshold']]
    behavioral = [k for k in CONFIG['behavioral_components'] if (components[k] or 0.0) >= CONFIG['support_threshold']]
    flagged = total >= CONFIG['score_threshold'] and len(supporting) >= CONFIG['min_supporting_components'] and bool(behavioral)
    return round(total, 4), flagged, supporting

def analyze(data, providers=None):
    """Returns (findings, link_rows). Requires provider_profiles; without them no pair is eligible.
    providers: evaluate only pairs in which one of these providers is the predecessor or the successor (stream enrichment)."""
    profiles = {p['provider_id']: p for p in data.get('provider_profiles', [])}
    by_provider = defaultdict(list)
    for c in sorted(data['claims'], key=lambda c: (c['service_date'], c['claim_id'])): by_provider[c['provider_id']].append(c)
    referrals = defaultdict(list)
    for r in data['referrals']: referrals[r['destination_provider_id']].append(r)
    history = defaultdict(list)
    for h in data['investigation_history']: history[h['provider_id']].append(h)
    acquisitions = {(r['source_id'], r['target_id']): r for r in data['relationships'] if r['relationship_type'] == 'practice_acquisition'}
    starts = sorted((p['enrollment_date'], pid) for pid, p in profiles.items() if p.get('enrollment_date'))
    outputs, links = [], []
    for pid_a, a in sorted(profiles.items()):
        if a['operating_status'] not in CONFIG['inactive_statuses'] or not a.get('status_effective_date'): continue
        status_day = a['status_effective_date']; window_a = status_day - timedelta(days=CONFIG['predecessor_window_days'])
        claims_a = [c for c in by_provider[pid_a] if window_a < c['service_date'] <= status_day]
        members_a = {c['member_id'] for c in claims_a}; codes_a = Counter(c['primary_code'] for c in claims_a)
        referrers_a = {r['source_provider_id'] for r in referrals[pid_a] if r['referral_date'] <= status_day}
        # Candidate generation uses the sorted enrollment index instead of comparing all provider pairs.
        for start, pid_b in starts:
            if not status_day < start <= status_day + timedelta(days=CONFIG['eligibility_days']) or pid_b == pid_a: continue
            if providers is not None and pid_a not in providers and pid_b not in providers: continue
            end_b = start + timedelta(days=CONFIG['successor_window_days'])
            claims_b = [c for c in by_provider[pid_b] if start <= c['service_date'] <= end_b]
            if len(claims_b) < CONFIG['min_successor_claims']: continue
            members_b = {c['member_id'] for c in claims_b}; codes_b = Counter(c['primary_code'] for c in claims_b)
            referrers_b = {r['source_provider_id'] for r in referrals[pid_b] if r['referral_date'] <= end_b}
            id_score, matched, compared = identifier_matches(a, profiles[pid_b])
            days = (start - status_day).days
            components = {'patient_overlap': jaccard(members_a, members_b), 'shared_identifiers': id_score if compared else None, 'billing_similarity': cosine(codes_a, codes_b), 'referral_overlap': jaccard(referrers_a, referrers_b), 'timing': round(max(0.0, 1 - days / CONFIG['eligibility_days']), 4)}
            components = {k: None if v is None else round(v, 4) for k, v in components.items()}
            score, flagged, supporting = score_pair(components)
            shared_members = sorted(members_a & members_b); shared_referrers = sorted(referrers_a & referrers_b)
            acquisition = acquisitions.get((pid_b, pid_a)) or acquisitions.get((pid_a, pid_b))
            details = {'predecessor_status': a['operating_status'], 'predecessor_status_date': status_day, 'successor_start': start, 'days_between': days, 'matched_identifiers': matched, 'compared_identifiers': compared, 'shared_member_count': len(shared_members), 'predecessor_members': len(members_a), 'successor_members': len(members_b), 'shared_procedure_codes': sorted(set(codes_a) & set(codes_b)), 'shared_referrers': shared_referrers, 'supporting_components': supporting, 'unavailable_components': [k for k, v in components.items() if v is None], 'documented_acquisition': acquisition['relationship_id'] if acquisition else None, 'predecessor_investigation_history': [h['investigation_id'] for h in history[pid_a]], 'windows': {'predecessor': [window_a + timedelta(days=1), status_day], 'successor': [start, end_b]}, 'detector_version': CONFIG['version']}
            link_id = stable_id('PS-', pid_a, pid_b); finding_id = None
            if flagged:
                shared_claims = [c for c in claims_b if c['member_id'] in members_a] or claims_b
                predecessor_claims = [c for c in claims_a if c['member_id'] in members_b][:5]
                sources = [('provider_successors', link_id, {'link_id': link_id, 'predecessor_id': pid_a, 'successor_id': pid_b, 'score': score, 'components': components, **details}), ('provider_profiles', pid_a, a), ('provider_profiles', pid_b, profiles[pid_b])]
                sources += [('claims', c['claim_id'], c) for c in shared_claims[:10]] + [('claims', c['claim_id'], c) for c in predecessor_claims]
                sources += [('referrals', r['referral_id'], r) for r in referrals[pid_b] if r['source_provider_id'] in shared_referrers and r['referral_date'] <= end_b][:10]
                if acquisition: sources.append(('relationships', acquisition['relationship_id'], acquisition))
                limitations = ['A similarity score indicates a possible operational connection to investigate; it does not establish misconduct by either provider.', 'Predecessor findings and allegations are not transferred; prior investigations are historical context only.']
                if acquisition: limitations.append(f'A documented practice acquisition ({acquisition["relationship_id"]}) may legitimately explain the overlap; request ownership/acquisition records.')
                severity = 'HIGH' if score >= 0.7 and a['operating_status'] in {'revoked', 'suspended'} and not acquisition else 'MEDIUM'
                completeness = round(sum(v is not None for v in components.values()) / len(COMPONENTS), 2)
                finding, ev = make_finding('phoenix_successor', shared_claims[:40], sources, f'Possible successor: {pid_b} began operating {days} days after {pid_a} was {a["operating_status"]}. Similarity score {score:.2f} (not a probability); supporting components: {", ".join(supporting)}.', severity, score, 'phoenix', context={'predecessor_id': pid_a, 'link_id': link_id}, limitations=limitations, entity=pid_b, version=CONFIG['version'], completeness=completeness)
                outputs.append((finding, ev)); finding_id = finding.finding_id
            links.append(dict(link_id=link_id, predecessor_id=pid_a, successor_id=pid_b, score=score, flagged=flagged, components=components, details=details, finding_id=finding_id, detector_version=CONFIG['version']))
    return outputs, links
