"""Persistent leased PostgreSQL jobs; SKIP LOCKED claiming and restart recovery."""
import logging
import os
import signal
import sys
import time
import threading
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, update, or_, and_
from app.core import engine, now, clean
from app.models import jobs

log=logging.getLogger(__name__)
HEARTBEAT=Path('/tmp/claimshield-worker-heartbeat')
worker_id=str(uuid4());stopping=threading.Event()

def enqueue(kind,payload=None):
    with engine.begin() as con:
        con.exec_driver_sql('SELECT pg_advisory_xact_lock(8231703)')
        existing=con.execute(select(jobs).where(jobs.c.status.in_(['QUEUED','RUNNING'])).order_by(jobs.c.created_at).limit(1)).mappings().first()
        if existing:return clean(dict(existing))
        row=dict(job_id='JOB-'+uuid4().hex[:16],kind=kind,status='QUEUED',payload=payload or {},progress=0,attempts=0)
        con.execute(jobs.insert().values(**row));return clean(row)

def claim_job():
    with engine.begin() as con:
        row=con.execute(select(jobs).where(or_(jobs.c.status=='QUEUED',and_(jobs.c.status=='RUNNING',jobs.c.lease_until<now()))).order_by(jobs.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
        if not row:return None
        if row['attempts']>=3:
            con.execute(update(jobs).where(jobs.c.job_id==row['job_id']).values(status='FAILED',error='Retry limit exhausted after interruption.',completed_at=now()));return None
        con.execute(update(jobs).where(jobs.c.job_id==row['job_id']).values(status='RUNNING',worker_id=worker_id,attempts=row['attempts']+1,lease_until=now()+timedelta(seconds=90),error=None))
        return dict(row)

def run_job(job):
    from app.services.ingestion import import_dataset
    from app.services.pipeline import analyze
    def progress(value):
        with engine.begin() as con:con.execute(update(jobs).where(jobs.c.job_id==job['job_id'],jobs.c.worker_id==worker_id).values(progress=float(value),lease_until=now()+timedelta(seconds=90)))
    finished=threading.Event()
    def heartbeat():
        while not finished.wait(10):
            HEARTBEAT.touch()
            with engine.begin() as con:con.execute(update(jobs).where(jobs.c.job_id==job['job_id'],jobs.c.worker_id==worker_id).values(lease_until=now()+timedelta(seconds=90)))
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        result={}
        if job['kind'] in {'import','all'}:result['import']=import_dataset(job['payload'].get('directory'),progress)
        if job['kind'] in {'analysis','train','all'}:result['analysis']=analyze(progress)
        with engine.begin() as con:con.execute(update(jobs).where(jobs.c.job_id==job['job_id'],jobs.c.worker_id==worker_id).values(status='COMPLETED',progress=100,result=clean(result),completed_at=now(),lease_until=None))
    except Exception as exc:
        log.exception('Job %s failed',job['job_id'])
        with engine.begin() as con:
            attempts=con.execute(select(jobs.c.attempts).where(jobs.c.job_id==job['job_id'])).scalar()
            con.execute(update(jobs).where(jobs.c.job_id==job['job_id'],jobs.c.worker_id==worker_id).values(status='FAILED' if isinstance(exc,ValueError) or attempts>=3 else 'QUEUED',error=str(exc)[:1000],lease_until=None))
    finally:finished.set();thread.join(timeout=2)

def main():
    if '--health' in sys.argv:
        sys.exit(0 if HEARTBEAT.exists() and time.time()-HEARTBEAT.stat().st_mtime<45 else 1)
    signal.signal(signal.SIGTERM,lambda *_:stopping.set())
    while not stopping.is_set():
        HEARTBEAT.touch()
        job=claim_job()
        if job:run_job(job)
        else:stopping.wait(2)

if __name__=='__main__':main()

