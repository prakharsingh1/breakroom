"""Authenticated sandbox control plane; never imports or executes uploaded code."""
import base64
import hashlib
import io
import json
import re
import zipfile
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, insert, select, update

from breakroom.scenarios import list_cases
from ..team_db import opaque_id, utcnow
from .config import SandboxSettings
from .db import credentials, deployments, jobs, trials, workers
from .github import import_github
from .sources import SourceError, validate_archive
from .vault import Vault


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class DeploymentInput(Input):
    name: str = Field(min_length=1, max_length=120)
    source_kind: Literal['zip', 'github']
    archive_base64: str | None = Field(default=None, max_length=1398104)
    repository: str | None = Field(default=None, max_length=160)
    commit_sha: str | None = Field(default=None, max_length=40)
    github_token: str | None = Field(default=None, max_length=255)

class KeyInput(Input):
    provider: Literal['openai', 'anthropic']
    secret: str = Field(min_length=20, max_length=255, pattern=r'^[A-Za-z0-9_.-]+$')

class RunInput(Input):
    deployment_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    cases: list[str] = Field(min_length=1, max_length=24)
    seeds: list[int] = Field(default=[0], min_length=1, max_length=3)
    repetitions: int = Field(default=1, ge=1, le=3)
    provider: Literal['openai', 'anthropic'] | None = None
    model: str | None = Field(default=None, max_length=120)
    max_calls: int = Field(default=20, ge=1, le=100)
    max_output_tokens: int = Field(default=512, ge=1, le=2048)
    accept_model_cost: bool = False


JOB_FIELDS = [c for c in jobs.c if c.name not in {'request_key', 'request_hash', 'submitted_by', 'credential_id'}]
DEPLOY_FIELDS = [c for c in deployments.c if c.name != 'source']
KEY_FIELDS = [credentials.c.id, credentials.c.provider, credentials.c.created_at]


def configure_sandbox(app, team_settings, store, authorize, *, settings=None):
    settings = settings or SandboxSettings.from_environment()
    settings.validate(team_settings.environment)
    app.state.sandbox_settings = settings
    vault = Vault(settings.vault_key) if settings.enabled else None
    catalog = {case['case_id']: case for case in list_cases()}
    base = '/api/team/projects/{project_id}/sandbox'

    def access(request, con, project_id, *, minimum='viewer', lock=False):
        project, role, who = authorize(request, con, project_id, minimum=minimum, lock=lock)
        if who['key']:
            raise HTTPException(403, 'Sandbox access requires a signed-in workspace member')
        return project, role, who

    def enabled():
        if not settings.enabled:
            raise HTTPException(503, 'Agent execution is not configured on this installation')

    def healthy(con):
        return con.execute(select(workers).where(workers.c.heartbeat_at > utcnow()-timedelta(seconds=90),
            workers.c.runtime == settings.runtime, workers.c.development_only == settings.development_only)).mappings().first()

    def job(con, project_id, job_id):
        row = con.execute(select(*JOB_FIELDS).where(jobs.c.project_id == project_id, jobs.c.id == job_id,
            jobs.c.expires_at > utcnow())).mappings().first()
        if not row:
            raise HTTPException(404, 'Sandbox run not found or retention expired')
        return dict(row)

    @app.get(base)
    def overview(project_id: str, request: Request):
        with store.engine.connect() as con:
            project, role, _ = access(request, con, project_id)
            worker = healthy(con) if settings.enabled else None
            return {'enabled': settings.enabled, 'available': bool(worker), 'development_only': settings.development_only,
                'runtime': settings.runtime, 'models': settings.models(),
                'notice': ('Trusted local fixtures only. This development runtime is not approved for untrusted customers.' if settings.development_only else
                           'Agent containers have no network. Optional model requests use a capped provider broker.'),
                'deployments': [dict(r) for r in con.execute(select(*DEPLOY_FIELDS).where(deployments.c.project_id==project_id).order_by(deployments.c.created_at.desc())).mappings()],
                'credentials': [dict(r) for r in con.execute(select(*KEY_FIELDS).where(credentials.c.project_id==project_id)).mappings()],
                'runs': [dict(r) for r in con.execute(select(*JOB_FIELDS).where(jobs.c.project_id==project_id, jobs.c.expires_at>utcnow()).order_by(jobs.c.created_at.desc()).limit(20)).mappings()],
                'limits': {'archive_bytes': 1048576, 'trials': 72, 'wall_seconds': 900, 'trial_seconds': 60, 'model_calls': 100, 'output_tokens_per_call': 2048}}

    @app.post(base+'/deployments', status_code=201)
    def deploy(project_id: str, body: DeploymentInput, request: Request):
        enabled()
        # Check access before network I/O; recheck under a project lock at commit.
        with store.engine.connect() as con:
            access(request, con, project_id, minimum='developer')
        try:
            if body.source_kind == 'zip':
                if not body.archive_base64 or any([body.repository, body.commit_sha, body.github_token]):
                    raise SourceError('ZIP deployment requires only its archive')
                package = validate_archive(base64.b64decode(body.archive_base64, validate=True))
            else:
                if body.archive_base64 or not body.repository or not body.commit_sha:
                    raise SourceError('GitHub deployment requires a repository and pinned commit')
                package = import_github(body.repository, body.commit_sha, body.github_token)
        except (ValueError, SourceError) as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, SourceError) else 'Invalid base64 ZIP') from None
        row = dict(id=opaque_id(), project_id=project_id, name=body.name.strip() or 'Agent', source_kind=body.source_kind,
            repository=body.repository, commit_sha=body.commit_sha.lower() if body.commit_sha else None,
            sha256=package.sha256, manifest=package.manifest, created_at=utcnow())
        with store.engine.begin() as con:
            access(request, con, project_id, minimum='developer', lock=True)
            if con.execute(select(func.count()).select_from(deployments).where(deployments.c.project_id==project_id)).scalar_one() >= 10:
                raise HTTPException(409, 'Delete an older deployment before adding another; limit 10')
            con.execute(insert(deployments).values(**row, source=vault.encrypt(package.archive, project_id, 'source', row['id'])))
        return row

    @app.delete(base+'/deployments/{deployment_id}')
    def delete_deployment(project_id: str, deployment_id: str, request: Request):
        with store.engine.begin() as con:
            access(request, con, project_id, minimum='developer', lock=True)
            active = con.execute(select(jobs.c.id).where(jobs.c.project_id==project_id, jobs.c.deployment_id==deployment_id,
                jobs.c.status.in_(['queued','running']))).first()
            if active:
                raise HTTPException(409, 'Cancel and finish active runs before deleting their deployment')
            con.execute(delete(deployments).where(deployments.c.project_id==project_id, deployments.c.id==deployment_id))
        return {'deleted': True}

    @app.post(base+'/credentials', status_code=201)
    def save_key(project_id: str, body: KeyInput, request: Request):
        enabled()
        row = dict(id=opaque_id(), project_id=project_id, provider=body.provider, created_at=utcnow())
        with store.engine.begin() as con:
            access(request, con, project_id, minimum='owner', lock=True)
            con.execute(delete(credentials).where(credentials.c.project_id==project_id, credentials.c.provider==body.provider))
            con.execute(insert(credentials).values(**row, secret=vault.encrypt(body.secret.encode(), project_id, 'key', row['id'])))
        return {key: row[key] for key in ('id','provider','created_at')}

    @app.delete(base+'/credentials/{credential_id}')
    def revoke_key(project_id: str, credential_id: str, request: Request):
        with store.engine.begin() as con:
            access(request, con, project_id, minimum='owner', lock=True)
            con.execute(delete(credentials).where(credentials.c.project_id==project_id, credentials.c.id==credential_id))
        return {'deleted': True}

    @app.post(base+'/runs', status_code=201)
    def create_run(project_id: str, body: RunInput, request: Request):
        enabled()
        request_key = request.headers.get('idempotency-key', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', request_key):
            raise HTTPException(422, 'A unique Idempotency-Key of 16..64 characters is required')
        if (len(set(body.cases)) != len(body.cases) or not set(body.cases) <= catalog.keys()
                or len(set(body.seeds)) != len(body.seeds) or not set(body.seeds) <= {0,1,2}):
            raise HTTPException(422, 'Select unique built-in drills and reviewed seeds 0, 1 or 2')
        plan = [{'case_id': case, 'seed': seed, 'trial': trial+1} for case in body.cases for seed in body.seeds for trial in range(body.repetitions)]
        if len(plan) > 72:
            raise HTTPException(422, 'A sandbox run supports at most 72 trials')
        if body.provider:
            if not body.accept_model_cost or body.model not in settings.models()[body.provider]:
                raise HTTPException(422, 'Select an approved model and accept provider usage charges')
        elif body.model or body.accept_model_cost:
            raise HTTPException(422, 'Choose a provider for model access')
        digest = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
        with store.engine.begin() as con:
            project, _, who = access(request, con, project_id, minimum='developer', lock=True)
            existing = con.execute(select(jobs.c.id, jobs.c.request_hash).where(jobs.c.project_id==project_id, jobs.c.request_key==request_key)).mappings().first()
            if existing:
                if existing['request_hash'] != digest:
                    raise HTTPException(409, 'Idempotency key already used with different settings')
                return job(con, project_id, existing['id'])
            if not healthy(con):
                raise HTTPException(503, 'No healthy configured sandbox worker is available')
            deployment = con.execute(select(deployments.c.id).where(deployments.c.id==body.deployment_id, deployments.c.project_id==project_id)).first()
            if not deployment:
                raise HTTPException(404, 'Agent deployment not found')
            active = con.execute(select(func.count()).select_from(jobs).where(jobs.c.project_id==project_id, jobs.c.status.in_(['queued','running']))).scalar_one()
            retained = con.execute(select(func.count()).select_from(jobs).where(jobs.c.project_id==project_id, jobs.c.expires_at>utcnow())).scalar_one()
            if active >= 2 or retained >= 20:
                raise HTTPException(409, 'Run limit reached: at most 2 active and 20 retained runs per project')
            credential = None
            if body.provider:
                credential = con.execute(select(credentials.c.id).where(credentials.c.project_id==project_id, credentials.c.provider==body.provider)).scalar()
                if not credential:
                    raise HTTPException(409, 'A project owner must add this provider key first')
            ident = opaque_id()
            con.execute(insert(jobs).values(id=ident, project_id=project_id, deployment_id=body.deployment_id,
                submitted_by=who['user']['id'], credential_id=credential, request_key=request_key, request_hash=digest,
                plan=plan, provider=body.provider, model=body.model, max_calls=body.max_calls if body.provider else 0,
                max_output_tokens=body.max_output_tokens, calls_used=0, usage={'input_tokens': 0, 'output_tokens': 0, 'unknown_calls': 0},
                status='queued', verdict='INCONCLUSIVE', cancel_requested=False, runtime=settings.runtime,
                created_at=utcnow(), expires_at=utcnow()+timedelta(days=project['retention_days'])))
            return job(con, project_id, ident)

    @app.get(base+'/runs/{job_id}')
    def read_run(project_id: str, job_id: str, request: Request):
        with store.engine.connect() as con:
            access(request, con, project_id)
            value = job(con, project_id, job_id)
            value['trials'] = [dict(r) for r in con.execute(select(trials).where(trials.c.job_id==job_id).order_by(trials.c.position)).mappings()]
            return value

    @app.post(base+'/runs/{job_id}/cancel')
    def cancel_run(project_id: str, job_id: str, request: Request):
        with store.engine.begin() as con:
            access(request, con, project_id, minimum='developer', lock=True)
            value = job(con, project_id, job_id)
            if value['status'] in {'queued','running'}:
                changes = {'cancel_requested': True}
                if value['status'] == 'queued':
                    changes.update(status='cancelled', finished_at=utcnow())
                con.execute(update(jobs).where(jobs.c.id==job_id).values(**changes))
            return job(con, project_id, job_id)

    @app.delete(base+'/runs/{job_id}')
    def delete_run(project_id: str, job_id: str, request: Request):
        with store.engine.begin() as con:
            access(request, con, project_id, minimum='developer', lock=True)
            value = job(con, project_id, job_id)
            if value['status'] in {'queued','running'}:
                raise HTTPException(409, 'Cancel and finish a run before deleting its evidence')
            con.execute(delete(jobs).where(jobs.c.id==job_id))
        return {'deleted': True}

    @app.get(base+'/starter.zip')
    def starter(project_id: str, request: Request):
        with store.engine.connect() as con:
            access(request, con, project_id)
        from pathlib import Path
        from breakroom import agents
        source = Path(agents.__file__).read_text().replace('from .models import', 'from breakroom.models import')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('agent.py', source)
            archive.writestr('breakroom-agent.json', json.dumps({'version':1, 'entrypoint':'agent:corrected',
                'capabilities':['payments','tickets','reconciliation','virtual_clock','events']}, indent=2))
            archive.writestr('README.md', 'Breakroom scripted adapter example. Replace agent.py with your agent. Change entrypoint to agent:faulty to reproduce duplicate refunds. Optional: context.model(prompt) returns text using the selected capped provider broker. No network or dependency installation. No safety guarantee.\n')
        return Response(stream.getvalue(), media_type='application/zip', headers={'Content-Disposition':'attachment; filename="breakroom-agent-starter.zip"', 'Cache-Control':'no-store'})
