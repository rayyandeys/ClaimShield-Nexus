"""Live stream runner: a dedicated lightweight process (compose service `stream`) so claim generation never occupies the
analytics worker.

Generator thread: owns the active session through a PostgreSQL lease, prepares the session cohort, persists about
`rate` claims per second with backpressure, schedules enrichment micro-batches as jobs on the existing worker queue and
drives lifecycle transitions. Processor thread: leases claims awaiting immediate screening (SKIP LOCKED) and runs
Layer A. Sessions, counters, leases and queues live in PostgreSQL; nothing needed for correctness is held only in memory.
"""
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, func
from app.core import engine, settings
from app import models as m
from app.services import stream as st

log = logging.getLogger(__name__)
HEARTBEAT = Path('/tmp/claimshield-stream-heartbeat')
runner_id = 'RUNNER-' + uuid4().hex[:12]
stopping = threading.Event()

def note(session_id, kind, message, **payload):
    with engine.begin() as con: st.emit(con, [st.event(session_id, kind, message, payload=payload)])

def sessions_awaiting_enrichment():
    with engine.connect() as con:
        return list(con.execute(select(m.stream_claims.c.session_id).where(m.stream_claims.c.analysis_status.in_(st.AWAITING_ENRICHMENT)).distinct()).scalars())

def generator_loop():
    next_due, paused, last_schedule, failures = {}, set(), {}, {}
    last_beat = last_check = 0.0
    while not stopping.is_set():
        try:
            clock = time.monotonic()
            if clock - last_beat >= 5: st.heartbeat(runner_id); HEARTBEAT.touch(); last_beat = clock
            session = st.acquire(runner_id)
            if session:
                sid = session['session_id']
                if session['status'] == 'STARTING' and not session['stop_requested']:
                    try: st.activate(session, runner_id); next_due[sid] = time.monotonic()
                    except Exception as exc:
                        failures[sid] = failures.get(sid, 0) + 1; log.exception('Session activation failed (%s)', failures[sid])
                        # Configuration/data problems fail the session at once; transient errors get five attempts.
                        if isinstance(exc, ValueError) or failures[sid] >= 5: st.fail_session(sid, f'Cohort preparation failed: {exc}')
                        else: stopping.wait(2)
                    continue
                state = st.advance(sid)
                if state == 'RUNNING':
                    rate = float(session['config']['rate_per_second']); due = next_due.setdefault(sid, time.monotonic())
                    if time.monotonic() >= due:
                        with engine.connect() as con: backlog = st.layer_a_backlog(con, sid)
                        if backlog >= settings.stream_max_backlog:
                            if sid not in paused: paused.add(sid); note(sid, 'backpressure_paused', f'Generation paused: {backlog} claims await immediate screening (limit {settings.stream_max_backlog}). Accepted claims are kept; none are discarded.', backlog=backlog)
                            next_due[sid] = time.monotonic() + .5
                        else:
                            if sid in paused: paused.discard(sid); note(sid, 'backpressure_resumed', f'Generation resumed: immediate-screening backlog down to {backlog}.', backlog=backlog)
                            st.generate_one(session, runner_id)
                            next_due[sid] = max(due + 1 / rate, time.monotonic() - 1 / rate)
            # Enrichment micro-batches: on the configured interval, and immediately while a session drains.
            if time.monotonic() - last_check >= 1:
                last_check = time.monotonic()
                for sid in sessions_awaiting_enrichment():
                    draining = session and session['session_id'] == sid and st.advance(sid) == 'DRAINING'
                    with engine.connect() as con: idle = st.layer_a_backlog(con, sid) == 0
                    if time.monotonic() - last_schedule.get(sid, 0) >= settings.stream_enrichment_interval_seconds or (draining and idle):
                        if st.enqueue_enrichment(sid, 'drain' if draining else f'every {settings.stream_enrichment_interval_seconds:g}s'): last_schedule[sid] = time.monotonic()
        except st.Conflict as exc:
            log.info('Generation skipped: %s', exc)
        except Exception:
            log.exception('Stream generator loop error'); stopping.wait(1)
        stopping.wait(.05)

def processor_loop():
    while not stopping.is_set():
        try: done = st.process_next(runner_id)
        except Exception: log.exception('Stream processor loop error'); done = None
        if not done: stopping.wait(.2)

def main():
    if '--health' in sys.argv:
        sys.exit(0 if HEARTBEAT.exists() and time.time() - HEARTBEAT.stat().st_mtime < 30 else 1)
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    log.info('Stream runner %s starting', runner_id)
    threads = [threading.Thread(target=generator_loop, name='generator', daemon=True), threading.Thread(target=processor_loop, name='processor', daemon=True)]
    for t in threads: t.start()
    while not stopping.wait(1): pass
    for t in threads: t.join(timeout=5)

if __name__ == '__main__': main()
