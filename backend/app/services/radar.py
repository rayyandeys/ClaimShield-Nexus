"""Compromised Member ID Radar: member risk signals and provider-level suspicious new-member batches.

All features use records dated on or before the analysis as-of date; relationships are evaluated strictly before
(or, for referrals, on) each member's first recent claim. Scenario labels are never read.
"""
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
import numpy as np
from app.services.detection import make_finding

CONFIG = json.loads((Path(__file__).resolve().parent.parent / 'config' / 'detectors.json').read_text())['member_radar']
SIGNALS = ['first_contact_burst', 'care_history_mismatch', 'geographic_jump_km', 'missing_encounter_rate', 'cross_provider_duplicates']
OPS = {'>=': lambda v, t: v >= t, '<=': lambda v, t: v <= t, '>': lambda v, t: v > t}

def haversine_km(a, b):
    if not a or not b or None in (*a, *b): return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))

def robust_z(values, minimum):
    """Median/MAD z-scores with a documented minimum scale so zero-variance signals cannot explode."""
    present = [v for v in values.values() if v is not None]
    if not present: return {k: None for k in values}
    med = float(np.median(present)); mad = float(np.median([abs(v - med) for v in present]))
    scale = max(1.4826 * mad, minimum)
    return {k: None if v is None else (v - med) / scale for k, v in values.items()}

class Index:
    """Per-member and per-provider lookups shared by member signals and batch detection."""
    def __init__(self, data):
        self.claims = sorted(data['claims'], key=lambda c: (c['service_date'], c['claim_id']))
        self.as_of = max(c['service_date'] for c in self.claims)
        self.recent_start = self.as_of - timedelta(days=CONFIG['recent_days'])
        self.baseline_start = self.recent_start - timedelta(days=CONFIG['baseline_days'])
        self.by_member = defaultdict(list); self.by_provider = defaultdict(list)
        for c in self.claims: self.by_member[c['member_id']].append(c); self.by_provider[c['provider_id']].append(c)
        self.referrals = defaultdict(list)
        for r in data['referrals']: self.referrals[(r['member_id'], r['destination_provider_id'])].append(r)
        self.encounters = defaultdict(list)
        for e in data['encounters']: self.encounters[(e['member_id'], e['provider_id'])].append(e)
        self.home = {m['member_id']: (m['latitude'], m['longitude']) for m in data.get('member_profiles', [])}
        self.location = {p['provider_id']: (p['latitude'], p['longitude']) for p in data.get('provider_profiles', [])}
        self.enrollment = {p['provider_id']: p['enrollment_date'] for p in data.get('provider_profiles', [])}
        self.facility = {p['provider_id']: p['facility_id'] for p in data['providers']}

    def distance(self, member_id, provider_id):
        return haversine_km(self.home.get(member_id), self.location.get(provider_id))

    def prior_relationship(self, member_id, provider_id, day):
        """First matching documented relationship before the member's first recent claim, or None."""
        if any(c['service_date'] < day for c in self.by_member[member_id] if c['provider_id'] == provider_id): return 'prior_claim'
        if any(e['service_start'].date() < day for e in self.encounters[(member_id, provider_id)]): return 'prior_encounter'
        if any(r['referral_date'] <= day for r in self.referrals[(member_id, provider_id)]): return 'referral'
        if any(c['service_date'] < day and c['facility_id'] == self.facility.get(provider_id) for c in self.by_member[member_id]): return 'prior_facility_claim'
        return None

    def prior_context(self, member_id, provider_id, code, day):
        return any(c['service_date'] < day and c['primary_code'] == code for c in self.by_member[member_id]) or any(r['referral_date'] <= day for r in self.referrals[(member_id, provider_id)])

def member_signals(ix, member_id):
    claims = ix.by_member[member_id]; recent = [c for c in claims if c['service_date'] > ix.recent_start]
    history = [c for c in claims if c['service_date'] <= ix.recent_start]
    burst_start = ix.as_of - timedelta(days=CONFIG['first_contact_days']); first = {}
    for c in claims: first.setdefault(c['provider_id'], c['service_date'])
    signals = {'first_contact_burst': {'value': float(sum(d > burst_start for d in first.values())), 'denominator': len(first), 'coverage': 'available'}}
    if recent:
        mismatched = [c for c in recent if not ix.prior_context(member_id, c['provider_id'], c['primary_code'], c['service_date']) and not any(o['service_date'] < c['service_date'] and o['provider_id'] == c['provider_id'] for o in claims)]
        signals['care_history_mismatch'] = {'value': len(mismatched) / len(recent), 'denominator': len(recent), 'coverage': 'proxy: prior same-code claim, same-provider claim or referral; diagnoses unavailable'}
        signals['missing_encounter_rate'] = {'value': sum(not c['encounter_id'] for c in recent) / len(recent), 'denominator': len(recent), 'coverage': 'available; a missing encounter is a documentation gap, not proof of a phantom service'}
        window = CONFIG['duplicate_window_days']
        dups = sum(any(o['provider_id'] != c['provider_id'] and o['primary_code'] == c['primary_code'] and abs((o['service_date'] - c['service_date']).days) <= window for o in claims) for c in recent)
        signals['cross_provider_duplicates'] = {'value': float(dups), 'denominator': len(recent), 'coverage': 'available'}
    else:
        for k in ['care_history_mismatch', 'missing_encounter_rate', 'cross_provider_duplicates']: signals[k] = {'value': None, 'denominator': 0, 'coverage': 'unavailable: no recent claims'}
    hist = [d for d in (ix.distance(member_id, c['provider_id']) for c in history) if d is not None]
    now = [d for d in (ix.distance(member_id, c['provider_id']) for c in recent) if d is not None]
    if hist and now: signals['geographic_jump_km'] = {'value': max(0.0, float(np.median(now)) - float(np.median(hist))), 'denominator': len(recent), 'coverage': 'available: offline haversine from synthetic coordinates'}
    else: signals['geographic_jump_km'] = {'value': None, 'denominator': len(recent), 'coverage': 'unavailable: missing coordinates or no historical care'}
    return signals

def member_scores(ix, members):
    raw = {m: member_signals(ix, m) for m in members}
    for name in SIGNALS:
        z = robust_z({m: s[name]['value'] for m, s in raw.items()}, CONFIG['min_scale'][name])
        for m, s in raw.items(): s[name]['robust_z'] = None if z[m] is None else round(min(max(z[m], 0.0), 5.0), 4)
    scores = {}
    for m, s in raw.items():
        available = [s[k]['robust_z'] for k in SIGNALS if s[k]['robust_z'] is not None]
        # At least two evaluable signals are required; missing signals are neither zero-risk nor high-risk.
        scores[m] = (round(float(np.mean(available)), 4) if len(available) >= 2 else None, s, len(available) / len(SIGNALS))
    return scores

def evaluate_check(name, value, evaluable=True):
    rule = CONFIG['checks'][name]
    return {'name': name, 'value': value, 'op': rule['op'], 'threshold': rule['threshold'], 'required': rule['required'], 'evaluable': evaluable and value is not None, 'passed': bool(evaluable and value is not None and OPS[rule['op']](value, rule['threshold']))}

def provider_batches(ix):
    candidates = {}
    for pid, claims in ix.by_provider.items():
        first = {}
        for c in claims: first.setdefault(c['member_id'], c)
        new = {m: c for m, c in first.items() if c['service_date'] > ix.recent_start}
        if len(new) < CONFIG['candidate_min_new_members']: continue
        baseline_new = sum(ix.baseline_start < c['service_date'] <= ix.recent_start for c in first.values())
        active_from = max(ix.baseline_start, claims[0]['service_date'], ix.enrollment.get(pid) or claims[0]['service_date'])
        active_days = (ix.recent_start - active_from).days
        rate = baseline_new * CONFIG['recent_days'] / max(active_days, 30) if active_days > 0 else 0.0
        members = []
        for m, c in sorted(new.items()):
            day = c['service_date']
            members.append({'member_id': m, 'first_recent_claim': day, 'claim_ids': [x['claim_id'] for x in claims if x['member_id'] == m and x['service_date'] > ix.recent_start], 'prior_relationship': ix.prior_relationship(m, pid, day), 'prior_context': ix.prior_context(m, pid, c['primary_code'], day), 'distance_km': ix.distance(m, pid)})
        distances = [x['distance_km'] for x in members if x['distance_km'] is not None]
        candidates[pid] = {'members': members, 'metrics': {'as_of': ix.as_of, 'recent_window': [ix.recent_start + timedelta(days=1), ix.as_of], 'baseline_window': [ix.baseline_start + timedelta(days=1), ix.recent_start], 'new_members': len(new), 'baseline_new_members': baseline_new, 'baseline_active_days': max(active_days, 0), 'historical_rate_per_window': round(rate, 3), 'growth_ratio': round(len(new) / max(rate, 1.0), 3), 'share_without_prior_relationship': round(sum(x['prior_relationship'] is None for x in members) / len(members), 4), 'share_with_prior_care_context': round(sum(x['prior_context'] for x in members) / len(members), 4), 'median_member_distance_km': round(float(np.median(distances)), 1) if distances else None, 'distance_coverage': len(distances) / len(members), 'recent_claims': sum(len(x['claim_ids']) for x in members)}}
    # Dispersion percentile relative to comparable providers that also acquired new members recently.
    peers = sorted(c['metrics']['median_member_distance_km'] for c in candidates.values() if c['metrics']['median_member_distance_km'] is not None and c['metrics']['new_members'] >= CONFIG['dispersion_peer_min_new_members'])
    for pid, cand in candidates.items():
        m = cand['metrics']; d = m['median_member_distance_km']
        m['geographic_dispersion_percentile'] = round(sum(p < d for p in peers) / len(peers), 4) if d is not None and len(peers) >= 5 else None
        cand['checks'] = [evaluate_check('new_members', m['new_members']), evaluate_check('growth_ratio', m['growth_ratio']), evaluate_check('share_without_prior_relationship', m['share_without_prior_relationship']), evaluate_check('share_with_prior_care_context', m['share_with_prior_care_context']), evaluate_check('geographic_dispersion_percentile', m['geographic_dispersion_percentile'])]
        cand['qualified'] = all(c['passed'] for c in cand['checks'] if c['required'])
    return candidates

def analyze(data):
    """Returns (findings, member_rows, batch_rows, batch_member_rows). Requires no profiles; geography degrades gracefully."""
    ix = Index(data); scores = member_scores(ix, sorted(ix.by_member)); claims = {c['claim_id']: c for c in data['claims']}
    candidates = provider_batches(ix); outputs = []; batch_rows = []; batch_member_rows = []
    member_rows = [dict(member_id=m, score=s, as_of=ix.as_of, signals={'signals': sig, 'coverage': cov}, detector_version=CONFIG['version']) for m, (s, sig, cov) in scores.items()]
    for pid, cand in sorted(candidates.items()):
        finding_id = None
        if cand['qualified']:
            batch_claims = [claims[cid] for x in cand['members'] for cid in x['claim_ids']]
            evaluable = [c for c in cand['checks'] if c['evaluable']]
            summary = {'provider_id': pid, **cand['metrics'], 'checks': cand['checks'], 'detector_version': CONFIG['version'], 'thresholds': CONFIG['checks']}
            sample = sorted(batch_claims, key=lambda c: c['claim_id'])[:20]
            profile = next((p for p in data.get('provider_profiles', []) if p['provider_id'] == pid), None)
            sources = [('radar_batches', pid, summary)] + ([('provider_profiles', pid, profile)] if profile else []) + [('claims', c['claim_id'], c) for c in sample]
            m = cand['metrics']
            finding, ev = make_finding('stolen_id_batch', batch_claims, sources, f'{m["new_members"]} previously unrelated members billed in the last {CONFIG["recent_days"]} days ({m["growth_ratio"]:.1f}× the historical acquisition rate); {m["share_without_prior_relationship"]:.0%} have no prior documented relationship and {m["share_with_prior_care_context"]:.0%} have prior care context. Requires member confirmation.', 'MEDIUM', m['growth_ratio'], 'radar', context={'checks': cand['checks'], 'notes': CONFIG['notes']}, limitations=['Suspicious member activity pattern; it does not establish that any identity was stolen or that any provider committed fraud.', 'Member-level lists are paginated in Member Radar; this evidence holds a bounded sample of source claims.', 'Severity rises only after reviewed member confirmations corroborate the pattern.'], entity=pid, version=CONFIG['version'], completeness=round(len(evaluable) / len(cand['checks']), 2))
            outputs.append((finding, ev)); finding_id = finding.finding_id
            for x in cand['members']:
                score, sig, cov = scores[x['member_id']]
                if score is None or score < CONFIG['member_finding_min_score']: continue
                mc = [claims[cid] for cid in x['claim_ids']]
                risk = {'member_id': x['member_id'], 'member_score': score, 'coverage': cov, **{k: {kk: sig[k][kk] for kk in ['value', 'robust_z', 'denominator']} for k in SIGNALS}}
                outputs.append(make_finding('member_id_possibly_compromised', mc, [('member_risk', x['member_id'], risk)] + [('claims', c['claim_id'], c) for c in mc], f'Member activity is unusual (robust member score {score:.2f}) and the member is part of a suspicious new-member batch at {pid}. Requires confirmation.', 'MEDIUM', score, 'radar', context={'batch_provider': pid, 'batch_finding_id': finding_id}, limitations=['Possibly compromised member record; a member confirmation is required before any conclusion.'], entity=x['member_id'], version=CONFIG['version'], completeness=round(cov, 2), entity_type='member'))
        batch_rows.append(dict(provider_id=pid, qualified=cand['qualified'], finding_id=finding_id, as_of=ix.as_of, metrics=cand['metrics'], checks=cand['checks'], detector_version=CONFIG['version']))
        for x in cand['members']: batch_member_rows.append(dict(provider_id=pid, member_id=x['member_id'], first_recent_claim=x['first_recent_claim'], claim_ids=x['claim_ids'], prior_relationship=x['prior_relationship'], prior_context=x['prior_context'], distance_km=x['distance_km'], member_score=scores[x['member_id']][0]))
    return outputs, member_rows, batch_rows, batch_member_rows
