"""Measured live-stream run against the running stack (writes data: it starts a real stream session).

    docker compose exec backend python -m app.stream_measure --claims 60 --rate 1

Samples the session status and two dashboard endpoints once per second until the session finishes, then writes
artifacts/evaluation/live_stream_measurement.json. Every number comes from the API and PostgreSQL; nothing is estimated.
"""
import argparse
import json
import logging
import time
import httpx
from app.core import settings

def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(); parser.add_argument('--claims', type=int, default=60); parser.add_argument('--rate', type=float, default=1.0)
    parser.add_argument('--mode', default='mixed_demo'); parser.add_argument('--stop-after', type=int, default=0, help='request Stop after this many generated claims (0 = run to completion)')
    args = parser.parse_args()
    with httpx.Client(base_url='http://backend:8000/api/v1', timeout=30) as api:
        started = api.post('/stream/sessions', json={'max_claims': args.claims, 'rate_per_second': args.rate, 'scenario_mode': args.mode}); started.raise_for_status()
        sid = started.json()['session_id']; samples = []; t0 = time.monotonic(); stop_sent = None
        while True:
            tick = time.monotonic(); s = api.get(f'/stream/sessions/{sid}').json(); status_ms = (time.monotonic() - tick) * 1000
            tick = time.monotonic(); api.get('/dataset/summary').raise_for_status(); summary_ms = (time.monotonic() - tick) * 1000
            tick = time.monotonic(); api.get('/siu/queue?capacity=10').raise_for_status(); queue_ms = (time.monotonic() - tick) * 1000
            m = s['metrics']
            samples.append({'t': round(time.monotonic() - t0, 1), 'status': s['status'], 'generated': m['generated'], 'accepted': m['accepted'], 'analyzed': m['analyzed'], 'fully_analyzed': m['fully_analyzed'], 'pending': m['pending'], 'awaiting_immediate': m['awaiting_immediate'], 'awaiting_enrichment': m['awaiting_enrichment'], 'failed': m['failed'], 'api_ms': {'session_status': round(status_ms, 1), 'dataset_summary': round(summary_ms, 1), 'siu_queue': round(queue_ms, 1)}})
            if args.stop_after and stop_sent is None and m['generated'] >= args.stop_after:
                stop_sent = api.post(f'/stream/sessions/{sid}/stop').json()
            if s['status'] in ('COMPLETED', 'STOPPED', 'FAILED'): break
            if time.monotonic() - t0 > 30 * 60: raise TimeoutError('Session did not finish within 30 minutes')
            time.sleep(max(0, 1 - (time.monotonic() - tick)))
        final = api.get(f'/stream/sessions/{sid}/summary').json()
    api_ms = {k: sorted(x['api_ms'][k] for x in samples) for k in samples[0]['api_ms']}
    result = {'session_id': sid, 'requested': {'claims': args.claims, 'rate_per_second': args.rate, 'mode': args.mode, 'stop_after': args.stop_after}, 'final_status': final['status'], 'wall_clock_seconds': samples[-1]['t'],
              'metrics': final['metrics'], 'findings': final['findings'], 'cases': final['cases'], 'stage_counts': final['stage_counts'], 'runs': final['runs'], 'stop_acknowledgement': stop_sent,
              'max_pending_observed': max(x['pending'] for x in samples), 'max_awaiting_immediate_observed': max(x['awaiting_immediate'] for x in samples),
              'api_latency_ms_during_stream': {k: {'median': v[len(v) // 2], 'p95': v[int(len(v) * .95) - 1 if len(v) > 1 else 0], 'max': v[-1]} for k, v in api_ms.items()},
              'samples': samples, 'note': 'Measured on the local Docker Compose stack; synthetic data. Session and claims remain in the database (append-only).'}
    path = settings.artifact_dir / 'evaluation' / 'live_stream_measurement.json'; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({k: v for k, v in result.items() if k != 'samples'}, indent=2, default=str))

if __name__ == '__main__': main()
