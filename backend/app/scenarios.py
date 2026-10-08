"""Scenario pack v1: deterministic, append-only augmentation of the original synthetic snapshot.

The original CSVs are copied unchanged; new providers/claims/encounters/referrals/relationships/history rows are
appended with new IDs, and two optional profile files are added. Scenario labels are written to a separate manifest
that no detection engine reads. All scenario activity is dated after 2025-10-01 so the Isolation Forest and
forecast training windows are unchanged.
"""
import csv
import hashlib
import json
import random
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from app.models import SPECS, OPTIONAL_SPECS

VERSION = 'scenario-pack-v1'
SEED = 20261009
# Public approximate city centroids; members/providers receive deterministic synthetic jitter around them.
CITY_COORDS = {'Phoenix': (33.4484, -112.0740), 'Los Angeles': (34.0522, -118.2437), 'Orlando': (28.5383, -81.3792), 'Atlanta': (33.7490, -84.3880), 'Chicago': (41.8781, -87.6298), 'Raleigh': (35.7796, -78.6382), 'Buffalo': (42.8864, -78.8784), 'Columbus': (39.9612, -82.9988), 'Houston': (29.7604, -95.3698), 'Seattle': (47.6062, -122.3321)}
SPECIALTY = {'dme': ('DME Supplier', 'dme', 'dme_supplier'), 'primary': ('Primary Care', 'professional', 'clinic'), 'bh': ('Behavioral Health', 'behavioral_health', 'behavioral_health_center'), 'lab': ('Clinical Laboratory', 'laboratory', 'laboratory')}
CODES = {'dme': [('SIM-DME-SUPPLY', .6, 150), ('SIM-DME-CATHETER', .4, 165)], 'primary': [('SIM-VISIT', .5, 120), ('SIM-CONSULT-L2', .3, 170), ('SIM-CONSULT-L1', .2, 110)], 'bh': [('SIM-BH-THERAPY', .6, 160), ('SIM-BH-EVAL', .4, 210)], 'lab': [('SIM-LAB-PANEL', .5, 140), ('SIM-LAB-SINGLE', .5, 60)]}
D = date.fromisoformat

def read(directory, name):
    with (directory / f'{name}.csv').open(newline='', encoding='utf-8-sig') as f: return list(csv.DictReader(f))

class Builder:
    def __init__(self, source):
        self.rng = random.Random(SEED)
        self.src = {name: read(source, name) for name in SPECS}
        self.new = {name: [] for name in SPECS}
        # Continue after the highest existing numeric ID (source IDs can have gaps, e.g. encounters).
        keys = {'claims': 'claim_id', 'claim_lines': 'claim_line_id', 'encounters': 'encounter_id', 'referrals': 'referral_id', 'relationships': 'relationship_id', 'investigation_history': 'investigation_id'}
        self.counters = {t: max(int(''.join(ch for ch in r[k] if ch.isdigit())) for r in self.src[t]) for t, k in keys.items()}
        self.slots = {}
        self.providers = {}
        self.profiles = {}
        self.facility_city = {f['facility_id']: f['city'] for f in self.src['facilities']}
        self.members_by_city = {}
        for m in sorted(self.src['members'], key=lambda m: m['member_id']): self.members_by_city.setdefault(m['city'], []).append(m)
        self.used_members = set()

    def next_id(self, table, prefix, width):
        self.counters[table] += 1; return f'{prefix}{self.counters[table]:0{width}d}'

    def jitter(self, city, spread=.06):
        lat, lon = CITY_COORDS[city]; return round(lat + self.rng.uniform(-spread, spread), 5), round(lon + self.rng.uniform(-spread, spread), 5)

    def facility(self, kind, city):
        typ = SPECIALTY[kind][2]
        matches = sorted(f['facility_id'] for f in self.src['facilities'] if f['facility_type'] == typ and f['city'] == city)
        if not matches: raise ValueError(f'No {typ} facility in {city}')
        return matches[0]

    def provider(self, pid, kind, city, start, status='active', status_date=None, owner=None, phone=None, bank=None, address=None):
        specialty, ptype, _ = SPECIALTY[kind]; fid = self.facility(kind, city); state = next(f['state'] for f in self.src['facilities'] if f['facility_id'] == fid)
        self.new['providers'].append(dict(provider_id=pid, provider_name=f'Synthetic Provider {pid[1:]}', provider_type=ptype, specialty=specialty, facility_id=fid, state=state, active_from=start, synthetic_identity='yes'))
        self.new['relationships'].append(dict(relationship_id=self.next_id('relationships', 'REL', 5), source_id=pid, source_type='provider', target_id=fid, target_type='facility', relationship_type='affiliated_with', effective_from=start, effective_to='', record_source='synthetic_provider_directory', verification_status='recorded'))
        lat, lon = self.jitter(city, .03)
        self.profiles[pid] = dict(provider_id=pid, address=address or f'{int(pid[1:]) * 7 + 100} Synthetic Medical Way, {city}', zip=f'SYN-{list(CITY_COORDS).index(city):02d}{int(pid[1:]):03d}', latitude=lat, longitude=lon, phone=phone or f'SYN-555-{int(pid[1:]):07d}', practice_owner_id=owner or f'PO-{pid}', bank_token=bank or f'SYNTH-BANK-TOKEN-{pid}', operating_status=status, status_effective_date=status_date or '', enrollment_date=start, profile_source=VERSION)
        self.providers[pid] = dict(kind=kind, city=city, facility=fid)

    def local_members(self, city, n, exclude=()):
        pool = [m['member_id'] for m in self.members_by_city[city] if m['member_id'] not in self.used_members and m['member_id'] not in exclude]
        chosen = sorted(self.rng.sample(pool, n)); self.used_members.update(chosen); return chosen

    def claim(self, pid, member, day, kind=None, code=None, encounter=True, units=None):
        p = self.providers[pid]; kind = kind or p['kind']; specialty, ctype, _ = SPECIALTY[kind]
        if code is None:
            r = self.rng.random(); acc = 0
            for c, share, _ in CODES[kind]:
                acc += share
                if r <= acc: code = c; break
            code = code or CODES[kind][-1][0]
        base = next(price for c, _, price in CODES[kind] if c == code)
        units = units or (self.rng.randint(1, 3) if kind == 'dme' else 1)
        unit_charge = round(base * self.rng.uniform(.9, 1.15), 2); billed = round(units * unit_charge, 2)
        duration = 20 if kind == 'dme' else 30
        slot = self.slots.get((pid, day), 0); self.slots[(pid, day)] = slot + 1
        start = datetime.combine(day, datetime.min.time()) + timedelta(hours=8, minutes=45 * slot)
        cid = self.next_id('claims', 'C', 7); eid = self.next_id('encounters', 'E', 7) if encounter else ''
        submitted = day + timedelta(days=self.rng.randint(0, 4)); allowed = round(billed * self.rng.uniform(.6, .7), 2)
        paid_status = submitted + timedelta(days=12) <= D('2026-09-30')
        paid = round(allowed * self.rng.uniform(.82, .88), 2) if paid_status else 0
        self.new['claims'].append(dict(claim_id=cid, member_id=member, provider_id=pid, facility_id=p['facility'], encounter_id=eid, service_date=day.isoformat(), service_start=start.isoformat(timespec='seconds'), service_duration_min=duration, claim_type=ctype, claim_status='paid' if paid_status else 'pending', submitted_date=submitted.isoformat(), payment_date=(submitted + timedelta(days=self.rng.randint(5, 12))).isoformat() if paid_status else '', primary_code=code, correction_of_claim_id='', related_claim_id='', complexity_band='low', billed_amount_usd=f'{billed:.2f}', allowed_amount_usd=f'{allowed:.2f}', paid_amount_usd=f'{paid:.2f}', member_responsibility_usd=f'{(allowed - paid) if paid_status else 0:.2f}'))
        self.new['claim_lines'].append(dict(claim_line_id=self.next_id('claim_lines', 'L', 8), claim_id=cid, procedure_code=code, unit_count=units, unit_charge_usd=f'{unit_charge:.2f}', line_billed_usd=f'{billed:.2f}', modifier='', days_supply='', days_since_last_fill='', line_role='service'))
        if encounter: self.new['encounters'].append(dict(encounter_id=eid, member_id=member, provider_id=pid, facility_id=p['facility'], service_start=start.isoformat(timespec='seconds'), duration_min=duration, documented_code=code, documented_complexity='low', encounter_source='synthetic_scheduling_record'))
        return cid

    def panel(self, pid, members, first, last, per_member=(2, 3)):
        span = (last - first).days
        for m in members:
            for _ in range(self.rng.randint(*per_member)): self.claim(pid, m, first + timedelta(days=self.rng.randint(0, span)))

    def referral(self, source, dest, member, day, reason='continuity_of_care'):
        self.new['referrals'].append(dict(referral_id=self.next_id('referrals', 'R', 6), source_provider_id=source, destination_provider_id=dest, destination_facility_id=self.providers[dest]['facility'], member_id=member, referral_date=day.isoformat(), referral_reason=reason, referral_source='synthetic_referral_record'))

    def build(self):
        rng = self.rng
        for pid, city in [('P0261', 'Columbus'), ('P0262', 'Columbus'), ('P0263', 'Houston'), ('P0264', 'Houston')]: self.provider(pid, 'primary', city, '2025-10-02')
        # Phoenix pair 1 (obvious): revoked DME supplier and a successor 23 days later sharing owner, phone and bank token.
        alpha = dict(owner='PO-SCN-ALPHA-HOLDINGS', phone='SYN-555-0199001', bank='SYNTH-BANK-TOKEN-ALPHA-0001')
        self.provider('P0251', 'dme', 'Columbus', '2025-10-02', 'revoked', '2026-03-01', **alpha)
        self.provider('P0252', 'dme', 'Columbus', '2026-03-24', **alpha, address='88 Commerce Park Drive Unit 4, Columbus')
        a1 = self.local_members('Columbus', 40)
        for m in a1: self.referral(rng.choice(['P0261', 'P0262', 'P0263']), 'P0251', m, D('2025-10-05') + timedelta(days=rng.randint(0, 20)), 'equipment')
        self.panel('P0251', a1, D('2025-10-27'), D('2026-02-27'))
        b1_old = a1[:30]; b1_new = self.local_members('Columbus', 2)
        for m in b1_old[:20]: self.referral(rng.choice(['P0261', 'P0262']), 'P0252', m, D('2026-03-26') + timedelta(days=rng.randint(0, 20)), 'equipment')
        self.panel('P0252', b1_old + b1_new, D('2026-04-01'), D('2026-06-20'), (1, 2))
        self.panel('P0252', b1_old[:10], D('2026-07-05'), D('2026-08-15'), (1, 1))
        # Stolen-ID batch at the same successor: ~270 previously unrelated, geographically dispersed members over six weeks.
        others = [m for city, ms in sorted(self.members_by_city.items()) if city != 'Columbus' for m in ms if m['member_id'] not in self.used_members]
        burst = sorted(rng.sample([m['member_id'] for m in others], 270)); self.used_members.update(burst)
        for m in burst: self.claim('P0252', m, D('2026-08-19') + timedelta(days=rng.randint(0, 41)), code='SIM-DME-CATHETER', encounter=False, units=rng.randint(2, 4))
        self.new['investigation_history'].append(dict(investigation_id=self.next_id('investigation_history', 'INV', 5), provider_id='P0251', facility_id=self.providers['P0251']['facility'], opened_date='2025-12-01', closed_date='2026-02-20', outcome='confirmed_improper_payment', outcome_available_date='2026-02-20', review_channel='synthetic_SIU', case_summary='Synthetic historical investigation outcome; not evidence about a real entity'))
        # Phoenix pair 2 (subtle): no shared identifiers; patients, billing mix and referrers carry over 40 days later.
        self.provider('P0253', 'primary', 'Houston', '2025-10-02', 'suspended', '2026-01-10')
        self.provider('P0254', 'primary', 'Houston', '2026-02-19')
        a2 = self.local_members('Houston', 50)
        for m in a2: self.referral(rng.choice(['P0263', 'P0264', 'P0261']), 'P0253', m, D('2025-10-03') + timedelta(days=rng.randint(0, 25)), 'consultation')
        self.panel('P0253', a2, D('2025-10-30'), D('2026-01-08'))
        b2 = a2[:42] + self.local_members('Houston', 4)
        for m in a2[:30]: self.referral(rng.choice(['P0263', 'P0264']), 'P0254', m, D('2026-02-20') + timedelta(days=rng.randint(0, 15)), 'continuity_of_care')
        self.panel('P0254', b2, D('2026-03-01'), D('2026-05-18'), (1, 2))
        # Benign look-alike 1: a laboratory moves into the closed behavioral-health clinic's address; nothing else matches.
        shared_address = '412 Synthetic Health Plaza, Columbus'
        self.provider('P0255', 'bh', 'Columbus', '2025-10-15', 'closed', '2026-02-01', address=shared_address)
        self.provider('P0256', 'lab', 'Columbus', '2026-03-15', address=shared_address)
        a3 = self.local_members('Columbus', 30)
        for m in a3: self.referral('P0264', 'P0255', m, D('2025-10-16') + timedelta(days=rng.randint(0, 20)), 'consultation')
        self.panel('P0255', a3, D('2025-11-10'), D('2026-01-30'))
        self.panel('P0256', self.local_members('Columbus', 30), D('2026-03-20'), D('2026-06-10'), (1, 2))
        # Benign look-alike 2: a documented practice acquisition after the predecessor retires.
        self.provider('P0257', 'primary', 'Raleigh', '2025-11-01', 'closed', '2026-04-30')
        self.provider('P0258', 'primary', 'Raleigh', '2026-05-15')
        a4 = self.local_members('Raleigh', 45)
        for m in a4: self.referral('P0261', 'P0257', m, D('2025-11-02') + timedelta(days=rng.randint(0, 20)), 'consultation')
        self.panel('P0257', a4, D('2025-11-25'), D('2026-04-28'))
        b4 = a4[:38] + self.local_members('Raleigh', 5)
        for m in a4[:25]: self.referral(rng.choice(['P0261', 'P0262']), 'P0258', m, D('2026-05-01') + timedelta(days=rng.randint(0, 12)), 'continuity_of_care')
        self.panel('P0258', b4, D('2026-05-20'), D('2026-08-10'), (1, 2))
        self.new['relationships'].append(dict(relationship_id=self.next_id('relationships', 'REL', 5), source_id='P0258', source_type='provider', target_id='P0257', target_type='provider', relationship_type='practice_acquisition', effective_from='2026-05-01', effective_to='', record_source='synthetic_ownership_filing', verification_status='recorded'))
        # Member Radar look-alike: an established practice absorbs a closing local practice's referred patients.
        self.provider('P0259', 'primary', 'Seattle', '2025-10-02')
        self.provider('P0260', 'primary', 'Seattle', '2025-10-05', 'closed', '2026-06-15')
        base = self.local_members('Seattle', 27)
        for i, m in enumerate(base): self.claim('P0259', m, D('2025-10-10') + timedelta(days=10 * i))
        closing = self.local_members('Seattle', 60)
        self.panel('P0260', closing, D('2025-10-20'), D('2026-06-10'))
        for m in closing[:55]: self.referral('P0260', 'P0259', m, D('2026-06-01') + timedelta(days=rng.randint(0, 13)), 'continuity_of_care')
        for m in closing[:55] + self.local_members('Seattle', 5): self.claim('P0259', m, D('2026-07-05') + timedelta(days=rng.randint(0, 80)))
        manifest = {'version': VERSION, 'seed': SEED, 'note': 'Evaluation labels only. Detection engines never read this file.', 'radar': [{'provider_id': 'P0252', 'expected_flag': True, 'scenario': 'stolen_id_batch', 'why': '270 previously unrelated, dispersed members billed catheter supplies in six weeks without encounters or referrals.'}, {'provider_id': 'P0259', 'expected_flag': False, 'scenario': 'legitimate_practice_closure', 'why': 'New members are local, referred by the closing practice and have prior same-service history.'}], 'phoenix': [{'predecessor_id': 'P0251', 'successor_id': 'P0252', 'expected_flag': True, 'scenario': 'obvious_successor'}, {'predecessor_id': 'P0253', 'successor_id': 'P0254', 'expected_flag': True, 'scenario': 'subtle_successor_no_shared_identifiers'}, {'predecessor_id': 'P0255', 'successor_id': 'P0256', 'expected_flag': False, 'scenario': 'address_only_lookalike'}, {'predecessor_id': 'P0257', 'successor_id': 'P0258', 'expected_flag': True, 'expected_disposition': 'explainable_by_documented_acquisition', 'scenario': 'legitimate_acquisition'}], 'radar_burst_members': len(burst)}
        return manifest

    def member_profiles(self):
        rows = []
        for m in sorted(self.src['members'], key=lambda m: m['member_id']):
            lat, lon = self.jitter(m['city']); rows.append(dict(member_id=m['member_id'], home_zip=f'SYN-{list(CITY_COORDS).index(m["city"]):02d}{int(m["member_id"][1:]) % 1000:03d}', latitude=lat, longitude=lon, profile_source=VERSION))
        return rows

    def provider_profiles(self):
        for p in sorted(self.src['providers'], key=lambda p: p['provider_id']):
            city = self.facility_city[p['facility_id']]; lat, lon = self.jitter(city, .03); n = int(p['provider_id'][1:])
            self.profiles.setdefault(p['provider_id'], dict(provider_id=p['provider_id'], address=f'{n * 7 + 100} Synthetic Medical Way, {city}', zip=f'SYN-{list(CITY_COORDS).index(city):02d}{n:03d}', latitude=lat, longitude=lon, phone=f'SYN-555-{n:07d}', practice_owner_id=f'PO-{p["provider_id"]}', bank_token=f'SYNTH-BANK-TOKEN-{p["provider_id"]}', operating_status='active', status_effective_date='', enrollment_date=p['active_from'], profile_source=VERSION))
        return [self.profiles[k] for k in sorted(self.profiles)]

def build_snapshot(source, output):
    """Write the augmented snapshot to output/snapshot and the label manifest to output. Returns a summary."""
    source, output = Path(source), Path(output)
    snapshot = output / 'snapshot'
    if snapshot.exists(): shutil.rmtree(snapshot)
    snapshot.mkdir(parents=True)
    builder = Builder(source); manifest = builder.build()
    for name, spec in {**SPECS, **OPTIONAL_SPECS}.items():
        rows = builder.member_profiles() if name == 'member_profiles' else builder.provider_profiles() if name == 'provider_profiles' else builder.src[name] + builder.new[name]
        with (snapshot / f'{name}.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=spec.split(), lineterminator='\n'); writer.writeheader(); writer.writerows(rows)
    manifest['appended_rows'] = {name: len(rows) for name, rows in builder.new.items() if rows}
    manifest['checksums'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(snapshot.glob('*.csv'))}
    (output / 'scenario_manifest.json').write_text(json.dumps(manifest, indent=2))
    return {'snapshot': str(snapshot), 'manifest': str(output / 'scenario_manifest.json'), 'appended_rows': manifest['appended_rows'], 'version': VERSION}
