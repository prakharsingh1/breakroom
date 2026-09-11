"""Dedicated trusted worker, never a web request handler or customer container.

Run with: python -m breakroom_api.sandbox.worker
Requires an operator configured Docker runtime and access to team PostgreSQL.
"""
from __future__ import annotations

import json
import hashlib
import signal
import threading
import time
from datetime import timedelta

from sqlalchemy import case, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from breakroom.evaluator import run_in_process
from breakroom.scenarios import load_case
from ..team_config import TeamSettings
from ..team_db import TeamStore, memberships, opaque_id, utcnow, users
from .config import SandboxSettings
from .db import credentials, deployments, jobs, trials, workers
from .providers import generate, ModelError
from .runtime import ContainerAgent, verify_runtime, reap_expired
from .sources import validate_archive
from .vault import Vault


def gate(reports, expected, *, complete):
    if any(report['verdict'] == 'FAIL' for report in reports):
        return 'FAIL'
    if complete and len(reports) == expected and all(report['verdict'] == 'PASS' for report in reports):
        return 'PASS'
    return 'INCONCLUSIVE'


class Worker:
    def __init__(self, store, settings, *, runtime_factory=ContainerAgent, provider_transport=None):
        settings.validate(store.settings.environment)
        if not settings.enabled:
            raise ValueError('Sandbox worker is disabled')
        self.store, self.settings = store, settings
        self.vault = Vault(settings.vault_key)
        self.id = opaque_id()
        self.scope = hashlib.sha256((store.settings.public_origin + ':' + store.settings.database_schema).encode()).hexdigest()[:32]
        self.last_reap = time.monotonic()
        self.image_id = None
        self.runtime_factory = runtime_factory
        self.provider_transport = provider_transport
        self.stop = threading.Event()

    def heartbeat(self):
        if time.monotonic() - self.last_reap > 10:
            reap_expired(self.scope)
            self.last_reap = time.monotonic()
        with self.store.engine.begin() as con:
            row = dict(id=self.id, runtime=self.settings.runtime, image_id=self.image_id,
                       development_only=self.settings.development_only, heartbeat_at=utcnow())
            con.execute(pg_insert(workers).values(**row).on_conflict_do_update(index_elements=[workers.c.id], set_=row))
            con.execute(delete(workers).where(workers.c.heartbeat_at < utcnow()-timedelta(days=1)))
            # Interrupted jobs are never replayed: a provider request may already be billed.
            con.execute(update(jobs).where(jobs.c.status=='running', jobs.c.heartbeat_at < utcnow()-timedelta(seconds=90)).values(
                status='error', verdict=case((jobs.c.verdict=='FAIL', 'FAIL'), else_='INCONCLUSIVE'), error='Worker heartbeat expired; incomplete run was not replayed', finished_at=utcnow()))
            con.execute(update(jobs).where(jobs.c.status=='queued', jobs.c.created_at < utcnow()-timedelta(minutes=15)).values(
                status='error', error='Queue wait limit exceeded', finished_at=utcnow()))

    def claim(self):
        with self.store.engine.begin() as con:
            row = con.execute(select(jobs).where(jobs.c.status=='queued', jobs.c.cancel_requested.is_(False),
                jobs.c.runtime==self.settings.runtime, jobs.c.expires_at>utcnow()).order_by(jobs.c.created_at).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if not row:
                return None
            con.execute(update(jobs).where(jobs.c.id==row['id']).values(status='running', worker_id=self.id,
                image_id=self.image_id, started_at=utcnow(), heartbeat_at=utcnow()))
            return dict(row)

    def allowed(self, con, row):
        if (not row or row['status']!='running' or row['worker_id']!=self.id or row['cancel_requested']
                or row['expires_at'] <= utcnow()):
            return False
        user = con.execute(select(users).where(users.c.id==row['submitted_by'])).mappings().first()
        if not user:
            return False
        if user['issuer']=='breakroom:development' and not self.store.settings.dev_login:
            return False
        if user['issuer']=='breakroom:password':
            from ..password_db import user_email_verified
            if not self.store.settings.password_enabled or (self.store.settings.password_require_verification and not user_email_verified(con,user)):
                return False
        role = con.execute(select(memberships.c.role).where(memberships.c.project_id==row['project_id'],
            memberships.c.user_id==row['submitted_by'])).scalar()
        return role in {'owner','developer'}

    def model_call(self, job_id, prompt, *, timeout_seconds=30):
        try:
            with self.store.engine.begin() as con:
                row = con.execute(select(jobs).where(jobs.c.id==job_id).with_for_update()).mappings().first()
                if not self.allowed(con, row) or not row['provider'] or row['model'] not in self.settings.models()[row['provider']]:
                    raise ModelError('Model access is unavailable for this run')
                credential = con.execute(select(credentials).where(credentials.c.id==row['credential_id'],
                    credentials.c.project_id==row['project_id'], credentials.c.provider==row['provider'])).mappings().first()
                if not credential or row['calls_used'] >= row['max_calls']:
                    raise ModelError('Model key revoked or call budget exhausted')
                key = self.vault.decrypt(credential['secret'], row['project_id'], 'key', credential['id']).decode()
                usage = dict(row['usage']); usage['unknown_calls'] += 1
                con.execute(update(jobs).where(jobs.c.id==job_id).values(calls_used=row['calls_used']+1, usage=usage))
            response = generate(row['provider'], row['model'], key, prompt, row['max_output_tokens'], transport=self.provider_transport, timeout_seconds=timeout_seconds)
            # Only known returned token counts are added; a failed/unknown response
            # retains its unknown-call count and is never represented as free.
            with self.store.engine.begin() as con:
                current = con.execute(select(jobs).where(jobs.c.id==job_id).with_for_update()).mappings().first()
                if current:
                    usage = dict(current['usage'])
                    if all(value is not None for value in response['usage'].values()):
                        usage['unknown_calls'] -= 1
                        for name, value in response['usage'].items(): usage[name] += value
                    con.execute(update(jobs).where(jobs.c.id==job_id).values(usage=usage))
            return response['text']
        except Exception:
            with self.store.engine.begin() as con:
                con.execute(update(jobs).where(jobs.c.id==job_id).values(error='Model request unavailable, revoked or over budget; coverage is incomplete'))
            raise ModelError('Model request unavailable, revoked or over budget') from None

    def execute(self, job):
        cancelled = threading.Event()
        monitor_stop = threading.Event()
        start = time.monotonic()
        def monitor():
            while not monitor_stop.is_set():
                try:
                    with self.store.engine.begin() as con:
                        row = con.execute(select(jobs).where(jobs.c.id==job['id'])).mappings().first()
                        if self.stop.is_set() or time.monotonic()-start >= 900 or not self.allowed(con, row):
                            cancelled.set()
                        else:
                            con.execute(update(jobs).where(jobs.c.id==job['id'], jobs.c.worker_id==self.id, jobs.c.status=='running').values(heartbeat_at=utcnow()))
                    self.heartbeat()
                except Exception:
                    cancelled.set()
                monitor_stop.wait(1)
        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        reports = []
        error = None
        try:
            with self.store.engine.connect() as con:
                current = con.execute(select(jobs).where(jobs.c.id==job['id'])).mappings().first()
                if not self.allowed(con, current):
                    cancelled.set()
                deployment = con.execute(select(deployments).where(deployments.c.id==job['deployment_id'], deployments.c.project_id==job['project_id'])).mappings().one()
                raw = self.vault.decrypt(deployment['source'], job['project_id'], 'source', deployment['id'])
                package = validate_archive(raw, github_prefix=deployment['source_kind']=='github')
                if package.sha256 != deployment['sha256']:
                    raise RuntimeError('Source digest does not match')
            for index, trial in enumerate(job['plan']):
                if cancelled.is_set():
                    break
                with self.runtime_factory(package, self.settings, image_id=self.image_id, scope=self.scope,
                        model=(lambda prompt, **options: self.model_call(job['id'], prompt, **options)) if job['provider'] else None) as agent:
                    report = run_in_process(load_case(trial['case_id']), agent, trial['seed'], deadline_seconds=60,
                        cancel_signal=cancelled, agent_metadata={'name':deployment['name'], 'version':deployment['commit_sha'],
                            'code_hash':deployment['sha256'], 'model':job['model']})
                if len(json.dumps(report).encode()) > 1024*1024:
                    raise RuntimeError('Trial evidence exceeds its storage limit')
                reports.append(report)
                with self.store.engine.begin() as con:
                    current = con.execute(select(jobs).where(jobs.c.id==job['id']).with_for_update()).mappings().first()
                    if not current or current['status']!='running' or current['worker_id']!=self.id:
                        return
                    con.execute(insert(trials).values(job_id=job['id'], position=index, report=report, created_at=utcnow()))
                    con.execute(update(jobs).where(jobs.c.id==job['id']).values(verdict=gate(reports,len(job['plan']),complete=False)))
        except Exception:
            error = 'Sandbox infrastructure or adapter protocol failed; remaining trials were not executed'
        finally:
            monitor_stop.set()
            watcher.join(timeout=12)
        with self.store.engine.begin() as con:
            current = con.execute(select(jobs).where(jobs.c.id==job['id']).with_for_update()).mappings().first()
            if not current or current['status']!='running' or current['worker_id']!=self.id:
                return
            error = error or current['error']
            status = 'cancelled' if cancelled.is_set() or current['cancel_requested'] else 'error' if error else 'completed'
            if status=='cancelled' and not error:
                error = 'Run cancelled, access revoked or wall time exhausted; partial evidence retained'
            con.execute(update(jobs).where(jobs.c.id==job['id']).values(status=status, error=error, finished_at=utcnow(),
                verdict=gate(reports,len(job['plan']),complete=status=='completed')))

    def serve(self, *, once=False):
        self.image_id = verify_runtime(self.settings)
        reap_expired(self.scope)
        self.heartbeat()
        try:
            while not self.stop.is_set():
                job = self.claim()
                if job:
                    self.execute(job)
                if once:
                    return bool(job)
                self.heartbeat()
                self.stop.wait(1)
        finally:
            with self.store.engine.begin() as con:
                con.execute(delete(workers).where(workers.c.id==self.id))


def main():
    settings = TeamSettings.from_environment()
    store = TeamStore(settings)
    store.migrate()
    worker = Worker(store, SandboxSettings.from_environment())
    for event in (signal.SIGINT, signal.SIGTERM):
        signal.signal(event, lambda *_: worker.stop.set())
    try:
        worker.serve()
    finally:
        store.engine.dispose()


if __name__ == '__main__':
    main()
