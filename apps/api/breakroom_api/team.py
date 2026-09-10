"""Authenticated PostgreSQL reporting control plane. Never executes adapters."""
from __future__ import annotations

import hmac
import asyncio
import json
import math
import re
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from .team_config import TeamSettings
from .team_db import (SCHEMA_VERSION, TeamStore, api_keys, memberships, opaque_id,
    projects, reports, suites, token_hash, users, utcnow)

ROLE_ORDER = {"viewer": 0, "developer": 1, "owner": 2}
KEY_SCOPES = frozenset({"reports:read", "reports:write", "suites:write"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    retention_days: int = Field(default=30, ge=1, le=365)


class ProjectPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    retention_days: int | None = Field(default=None, ge=1, le=365)


class MemberInput(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "developer", "owner"]


class RoleInput(StrictModel):
    role: Literal["viewer", "developer", "owner"]


class KeyInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[Literal["reports:read", "reports:write", "suites:write"]] = Field(min_length=1, max_length=3)
    expires_days: int = Field(default=30, ge=1, le=365)


class CaseMetadata(StrictModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    case_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SuiteInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    cases: list[CaseMetadata] = Field(min_length=1, max_length=128)


class CompareInput(StrictModel):
    baseline_id: str = Field(min_length=1, max_length=36)
    candidate_id: str = Field(min_length=1, max_length=36)


class LimitsMiddleware:
    """Single-worker request budgets; body bound enforced before JSON parsing."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.windows = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        client = (scope.get("client") or ("unknown", 0))[0]
        now = time.monotonic()
        while self.windows and next(iter(self.windows.values()))[0] < now - 60:
            self.windows.popitem(last=False)
        since, count = self.windows.get(client, (now, 0))
        if count >= self.settings.rate_limit_per_minute:
            return await JSONResponse({"detail": "Request rate limit exceeded"}, 429, headers={"Retry-After": "60"})(scope, receive, send)
        if len(self.windows) >= 10000 and client not in self.windows:
            return await JSONResponse({"detail": "Service request capacity reached"}, 429)(scope, receive, send)
        self.windows[client] = (since, count + 1)
        if len(scope.get("query_string", b"")) > 8192 or len(scope.get("raw_path", b"")) > 2048:
            return await JSONResponse({"detail": "Request URL exceeds bounds"}, 414)(scope, receive, send)
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
        if length < 0 or length > self.settings.max_request_bytes:
            return await JSONResponse({"detail": "Request body exceeds 2 MiB"}, 413)(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(self.settings.body_read_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > self.settings.max_request_bytes:
                        return await JSONResponse({"detail": "Request body exceeds 2 MiB"}, 413)(scope, receive, send)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"detail": "Request body read deadline exceeded"}, 408)(scope, receive, send)
        if body:
            if headers.get(b"content-type", b"").split(b";", 1)[0].lower() != b"application/json":
                return await JSONResponse({"detail": "Request body must use application/json"}, 415)(scope, receive, send)
            try:
                def unique_pairs(items):
                    value = {}
                    for key, child in items:
                        if key in value:
                            raise ValueError("duplicate key")
                        value[key] = child
                    return value
                def reject_constant(value):
                    raise ValueError("nonfinite JSON number")
                parsed = json.loads(body, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
                stack, count = [(parsed, 0)], 0
                while stack:
                    value, depth = stack.pop()
                    count += 1
                    if count > 250000 or depth > 64:
                        raise ValueError("JSON structure bound")
                    if isinstance(value, float) and not math.isfinite(value):
                        raise ValueError("nonfinite JSON number")
                    if isinstance(value, dict):
                        stack.extend((child, depth + 1) for child in value.values())
                    elif isinstance(value, list):
                        stack.extend((child, depth + 1) for child in value)
            except (ValueError, TypeError, RecursionError, UnicodeError):
                return await JSONResponse({"detail": "Request must contain bounded finite JSON with unique object keys"}, 422)(scope, receive, send)
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = message.get("headers", []) + [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
            await send(message)
        return await self.app(scope, replay, secure_send)


def user_view(user):
    return {key: user[key] for key in ("id", "email", "display_name")}


def project_view(project, role):
    return {key: project[key] for key in ("id", "name", "retention_days", "created_at")} | {"role": role}


def report_view(row, *, duplicate=False):
    return {key: row[key] for key in ("id", "project_id", "case_id", "verdict", "created_at", "expires_at")} | {
        "provenance": "customer_generated", "privacy": row["upload"]["privacy"], "duplicate": duplicate}


def create_app(settings: TeamSettings | None = None, store: TeamStore | None = None, *, billing_provider=None):
    settings = settings or TeamSettings.from_environment()
    settings.validate()
    store = store or TeamStore(settings)
    @asynccontextmanager
    async def lifespan(app):
        store.migrate()
        store.cleanup_expired()
        stop = asyncio.Event()
        app.state.retention_status = "ready"
        async def sweep():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.retention_sweep_seconds)
                except TimeoutError:
                    try:
                        await asyncio.to_thread(store.cleanup_expired)
                        app.state.retention_status = "ready"
                    except Exception:
                        # The health response exposes the retry state. Database
                        # exceptions may contain connection data; never log them.
                        app.state.retention_status = "retrying"
        sweeper = asyncio.create_task(sweep())
        async def reconcile_billing():
            app.state.billing_worker_status = "ready"
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.billing_worker_seconds)
                except TimeoutError:
                    try:
                        for _ in range(4):
                            if not await asyncio.to_thread(app.state.billing.process_one):
                                break
                        app.state.billing_worker_status = "ready"
                    except Exception:
                        # Durable inbox leases expire, making an interrupted
                        # reconciliation eligible for a bounded later retry.
                        app.state.billing_worker_status = "retrying"
                        continue
        billing_worker = asyncio.create_task(reconcile_billing()) if app.state.billing.enabled else None
        try:
            yield
        finally:
            stop.set()
            await sweeper
            if billing_worker:
                await billing_worker
            store.engine.dispose()
    app = FastAPI(title="Breakroom — Team reporting", lifespan=lifespan)
    app.state.settings, app.state.store = settings, store
    app.add_middleware(LimitsMiddleware, settings=settings)
    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(request, exc):
        # Pydantic's default response includes raw rejected input. The control
        # plane may receive accidentally pasted secrets, so never echo it.
        return JSONResponse({"detail": [{"type": item["type"], "msg": item["msg"]}
            for item in exc.errors()]}, status_code=422)
    @app.exception_handler(DBAPIError)
    async def sanitized_database_error(request, exc):
        return JSONResponse({"detail": "Team storage is temporarily unavailable; retry the same request safely."}, status_code=503)
    from .team_auth import configure_auth, principal
    configure_auth(app, settings, store)
    from .password_auth import configure_password_auth
    from .password_db import public_user, user_email_verified
    configure_password_auth(app, settings, store)

    def identity(request, con, *, optional=False, allow_unverified=False):
        auth = request.headers.get("authorization")
        if auth:
            if not auth.startswith("Bearer ") or len(auth) > 256:
                raise HTTPException(401, "Invalid bearer authentication")
            row = con.execute(select(api_keys).where(api_keys.c.token_hash == token_hash(auth[7:]),
                api_keys.c.revoked_at.is_(None), api_keys.c.expires_at > utcnow())).mappings().first()
            if not row:
                raise HTTPException(401, "API key is invalid, expired or revoked")
            user = con.execute(select(users).where(users.c.id == row["user_id"])).mappings().first()
            membership = con.execute(select(memberships.c.role).where(memberships.c.user_id == row["user_id"], memberships.c.project_id == row["project_id"])).first()
            if not user or not membership:
                raise HTTPException(401, "API key creator no longer has project membership")
            if user["issuer"] == "breakroom:development" and not settings.dev_login:
                raise HTTPException(401, "Development credentials are disabled")
            if user["issuer"] == "breakroom:password" and not settings.password_enabled:
                raise HTTPException(401, "Password credentials are disabled")
            if settings.password_require_verification and not user_email_verified(con, user):
                raise HTTPException(403, "Verify your email before accessing the workspace")
            return {"user": dict(user), "key": dict(row), "csrf_token": None}
        try:
            session = principal(request, store)
        except HTTPException as exc:
            if optional and exc.status_code == 401:
                return None
            raise
        if not session:
            if optional:
                return None
            raise HTTPException(401, "Sign in is required")
        if settings.password_require_verification and not allow_unverified and not user_email_verified(con, session["user"]):
            raise HTTPException(403, "Verify your email before accessing the workspace")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != settings.public_origin:
                raise HTTPException(403, "A same-origin request is required")
            supplied = request.headers.get("x-csrf-token", "")
            if not supplied or not hmac.compare_digest(supplied.encode(), session["csrf_token"].encode()):
                raise HTTPException(403, "CSRF token is missing or invalid")
        return {**session, "key": None}

    def authorize(request, con, project_id, *, minimum="viewer", scope="reports:read", lock=False):
        who = identity(request, con)
        query = select(projects).where(projects.c.id == project_id)
        if lock:
            query = query.with_for_update()
        project = con.execute(query).mappings().first()
        membership = con.execute(select(memberships.c.role).where(memberships.c.project_id == project_id, memberships.c.user_id == who["user"]["id"])).first()
        if not project or not membership:
            raise HTTPException(404, "Project not found")
        if ROLE_ORDER[membership[0]] < ROLE_ORDER[minimum]:
            raise HTTPException(403, "Your project role does not permit this action")
        if who["key"] and (who["key"]["project_id"] != project_id or scope not in who["key"]["scopes"]):
            raise HTTPException(403, "API key scope does not permit this action")
        return dict(project), membership[0], who

    def report_for(con, project_id, report_id):
        row = con.execute(select(reports).where(reports.c.id == report_id, reports.c.project_id == project_id,
            reports.c.expires_at > utcnow())).mappings().first()
        if not row:
            raise HTTPException(404, "Report not found or its retention period expired")
        return dict(row)

    from .team_billing import configure_billing
    billing = configure_billing(app, settings, store, authorize, provider=billing_provider)
    from .team_workspace import configure_workspace
    configure_workspace(app, settings, store, identity, authorize, billing, project_view, report_view)

    @app.get("/api/team/health")
    def health():
        with store.engine.connect() as con:
            con.execute(text("SELECT 1"))
        return {"status": "ok", "service": "breakroom-team", "database": "ready", "schema_version": SCHEMA_VERSION,
            "retention": getattr(app.state, "retention_status", "not_started"),
            "mail": {"configured": getattr(app.state, "password_mailer", None) is not None,
                "status": getattr(app.state, "password_mail_status", "disabled")},
            "billing": billing.health() | {"worker": getattr(app.state, "billing_worker_status", "disabled")}}

    @app.get("/api/team/me")
    def me(request: Request):
        with store.engine.connect() as con:
            who = identity(request, con, optional=True, allow_unverified=True)
            values = []
            if who and (not settings.password_require_verification or user_email_verified(con, who["user"])):
                query = select(projects, memberships.c.role).join(memberships).where(memberships.c.user_id == who["user"]["id"])
                if who["key"]:
                    query = query.where(projects.c.id == who["key"]["project_id"])
                values = [project_view(row, row["role"]) for row in con.execute(query.order_by(projects.c.created_at)).mappings()]
            user = public_user(con, who["user"]) if who else None
        return {"user": user, "csrf_token": who["csrf_token"] if who else None,
                "projects": values, "auth": {"oidc_available": bool(settings.oidc_issuer and settings.oidc_client_id), "dev_login_available": settings.dev_login,
                "password_available": settings.password_enabled, "mail_available": settings.mail_available,
                "verification_required": settings.password_require_verification}}

    @app.get("/api/team/projects")
    def list_projects(request: Request):
        with store.engine.connect() as con:
            identity(request, con)
        value = me(request)
        if not value["user"]:
            raise HTTPException(401, "Sign in is required")
        return {"items": value["projects"]}

    @app.post("/api/team/projects", status_code=201)
    def new_project(body: ProjectInput, request: Request):
        with store.engine.begin() as con:
            who = identity(request, con)
            if who["key"]:
                raise HTTPException(403, "Project creation requires an authenticated user session")
            if con.execute(select(func.count()).select_from(memberships).where(memberships.c.user_id == who["user"]["id"])).scalar_one() >= 100:
                raise HTTPException(409, "Local beta limit of 100 projects per user reached")
            value = {"id": opaque_id(), "name": body.name, "retention_days": body.retention_days, "created_at": utcnow()}
            con.execute(insert(projects).values(**value))
            con.execute(insert(memberships).values(project_id=value["id"], user_id=who["user"]["id"], role="owner"))
            return project_view(value, "owner")

    @app.get("/api/team/projects/{project_id}")
    def get_project(project_id: str, request: Request):
        with store.engine.connect() as con:
            project, role, _ = authorize(request, con, project_id)
            return project_view(project, role)

    @app.patch("/api/team/projects/{project_id}")
    def edit_project(project_id: str, body: ProjectPatch, request: Request):
        with store.engine.begin() as con:
            project, role, _ = authorize(request, con, project_id, minimum="owner", scope="owner:manage", lock=True)
            changes = body.model_dump(exclude_none=True)
            if not changes:
                raise HTTPException(422, "Provide a name or retention period")
            con.execute(update(projects).where(projects.c.id == project_id).values(**changes))
            if body.retention_days is not None:
                for row in con.execute(select(reports.c.id, reports.c.created_at, reports.c.expires_at).where(reports.c.project_id == project_id)).mappings():
                    shortened = row["created_at"] + timedelta(days=body.retention_days)
                    if shortened < row["expires_at"]:
                        con.execute(update(reports).where(reports.c.id == row["id"]).values(expires_at=shortened))
            return project_view(project | changes, role)

    @app.delete("/api/team/projects/{project_id}")
    def delete_project(project_id: str, request: Request):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:manage", lock=True)
            billing.check_deletion(con, project_id)
            con.execute(delete(projects).where(projects.c.id == project_id))
        return {"deleted": True, "project_id": project_id}

    @app.get("/api/team/projects/{project_id}/members")
    def list_members(project_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id, scope="session:members")
            rows = con.execute(select(users.c.id.label("user_id"), users.c.email, users.c.display_name, memberships.c.role).join(memberships).where(memberships.c.project_id == project_id)).mappings()
            return {"items": [dict(row) for row in rows]}

    @app.post("/api/team/projects/{project_id}/members", status_code=201)
    def add_member(project_id: str, body: MemberInput, request: Request):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:manage", lock=True)
            query = select(users).where(users.c.email == body.email.casefold())
            if not settings.dev_login:
                query = query.where(users.c.issuer != "breakroom:development")
            user = con.execute(query).mappings().first()
            if not user:
                raise HTTPException(404, "No existing verified account has that email; no invitation was sent")
            if user["issuer"] == "breakroom:password" and not user_email_verified(con, user):
                raise HTTPException(404, "No existing verified account has that email; no invitation was sent")
            if con.execute(select(memberships).where(memberships.c.project_id == project_id, memberships.c.user_id == user["id"])).first():
                raise HTTPException(409, "User is already a member; update their role instead")
            billing.enforce(con, project_id, added_seats=1)
            con.execute(insert(memberships).values(project_id=project_id, user_id=user["id"], role=body.role))
            return {"user_id": user["id"], "email": user["email"], "display_name": user["display_name"], "role": body.role}

    def modify_member(project_id, user_id, request, new_role):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:manage", lock=True)
            member = con.execute(select(memberships).where(memberships.c.project_id == project_id, memberships.c.user_id == user_id)).mappings().first()
            if not member:
                raise HTTPException(404, "Member not found")
            if member["role"] == "owner" and new_role != "owner":
                owners = con.execute(select(func.count()).select_from(memberships).where(memberships.c.project_id == project_id, memberships.c.role == "owner")).scalar_one()
                if owners <= 1:
                    raise HTTPException(409, "The last owner cannot be removed or demoted")
            if new_role is None:
                con.execute(delete(memberships).where(memberships.c.project_id == project_id, memberships.c.user_id == user_id))
                con.execute(update(api_keys).where(api_keys.c.project_id == project_id, api_keys.c.user_id == user_id).values(revoked_at=utcnow()))
            else:
                con.execute(update(memberships).where(memberships.c.project_id == project_id, memberships.c.user_id == user_id).values(role=new_role))
            return {"user_id": user_id, "role": new_role, "deleted": new_role is None}

    @app.patch("/api/team/projects/{project_id}/members/{user_id}")
    def edit_member(project_id: str, user_id: str, body: RoleInput, request: Request):
        return modify_member(project_id, user_id, request, body.role)

    @app.delete("/api/team/projects/{project_id}/members/{user_id}")
    def remove_member(project_id: str, user_id: str, request: Request):
        return modify_member(project_id, user_id, request, None)

    def key_view(row):
        return {key: row[key] for key in ("id", "name", "prefix", "scopes", "created_at", "expires_at", "revoked_at")}

    @app.get("/api/team/projects/{project_id}/keys")
    def list_keys(project_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:manage")
            return {"items": [key_view(row) for row in con.execute(select(api_keys).where(api_keys.c.project_id == project_id)).mappings()]}

    @app.post("/api/team/projects/{project_id}/keys", status_code=201)
    def create_key(project_id: str, body: KeyInput, request: Request):
        with store.engine.begin() as con:
            _, _, who = authorize(request, con, project_id, minimum="owner", scope="owner:manage")
            secret = "brk_" + secrets.token_urlsafe(32)
            now = utcnow()
            row = {"id": opaque_id(), "project_id": project_id, "user_id": who["user"]["id"], "name": body.name,
                "prefix": secret[:12], "token_hash": token_hash(secret), "scopes": sorted(set(body.scopes)),
                "created_at": now, "expires_at": now + timedelta(days=body.expires_days), "revoked_at": None}
            con.execute(insert(api_keys).values(**row))
            return key_view(row) | {"secret": secret}

    @app.delete("/api/team/projects/{project_id}/keys/{key_id}")
    def revoke_key(project_id: str, key_id: str, request: Request):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:manage")
            result = con.execute(update(api_keys).where(api_keys.c.id == key_id, api_keys.c.project_id == project_id).values(revoked_at=utcnow()))
            if not result.rowcount:
                raise HTTPException(404, "API key not found")
        return {"revoked": True, "id": key_id}

    @app.get("/api/team/projects/{project_id}/reports")
    def list_reports(project_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id)
            rows = con.execute(select(reports).where(reports.c.project_id == project_id, reports.c.expires_at > utcnow()).order_by(reports.c.created_at.desc()).limit(200)).mappings()
            return {"items": [report_view(row) for row in rows]}

    @app.post("/api/team/projects/{project_id}/reports")
    async def import_report(project_id: str, request: Request):
        from breakroom.uploads import validate_upload_envelope, upload_digest
        from fastapi.encoders import jsonable_encoder
        # Reject unauthorized callers before spending the bounded JSON-validation
        # budget, then recheck authorization under the insertion transaction.
        with store.engine.connect() as con:
            authorize(request, con, project_id, minimum="developer", scope="reports:write")
        idem = request.headers.get("idempotency-key", "")
        if not re.fullmatch(r"[a-f0-9]{64}", idem):
            raise HTTPException(422, "Idempotency-Key must be a 64-character lowercase hexadecimal digest")
        try:
            raw = await request.body()
            def pairs(items):
                value = {}
                for key, child in items:
                    if key in value:
                        raise ValueError("Duplicate JSON keys are not accepted")
                    value[key] = child
                return value
            upload = validate_upload_envelope(json.loads(raw, object_pairs_hook=pairs))
            digest = upload_digest(upload)
        except (ValueError, TypeError, RecursionError) as exc:
            raise HTTPException(422, "Invalid minimized report upload; prepare and validate it locally before importing.") from exc
        with store.engine.begin() as con:
            project, _, who = authorize(request, con, project_id, minimum="developer", scope="reports:write", lock=True)
            existing = con.execute(select(reports).where(reports.c.project_id == project_id, reports.c.idempotency_key == idem)).mappings().first()
            if existing:
                if existing["payload_hash"] != digest:
                    raise HTTPException(409, "Idempotency key was already used for a different report")
                if existing["expires_at"] <= utcnow():
                    raise HTTPException(410, "The original imported report has expired")
                return JSONResponse(jsonable_encoder(report_view(existing, duplicate=True)), status_code=200)
            billing.enforce(con, project_id, upload=upload)
            count = con.execute(select(func.count()).select_from(reports).where(reports.c.project_id == project_id)).scalar_one()
            if count >= 1000:
                raise HTTPException(409, "Local beta limit of 1000 retained reports reached; remove expired reports")
            now = utcnow()
            row = {"id": opaque_id(), "project_id": project_id, "submitted_by": who["user"]["id"], "idempotency_key": idem,
                "payload_hash": digest, "case_id": upload["report"]["case"]["case_id"], "verdict": upload["report"]["verdict"],
                "upload": upload, "created_at": now, "expires_at": now + timedelta(days=project["retention_days"])}
            con.execute(insert(reports).values(**row))
            return JSONResponse(jsonable_encoder(report_view(row)), status_code=201)

    @app.get("/api/team/projects/{project_id}/reports/{report_id}")
    def get_report(project_id: str, report_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id)
            row = report_for(con, project_id, report_id)
            return report_view(row) | {"upload": row["upload"]}

    @app.get("/api/team/projects/{project_id}/reports/{report_id}/evidence")
    def evidence(project_id: str, report_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id)
            report = report_for(con, project_id, report_id)["upload"]["report"]
            return {key: report.get(key) for key in ("events", "initial_state", "final_state", "checks", "agent_result")}

    @app.get("/api/team/projects/{project_id}/reports/{report_id}/export")
    def export_report(project_id: str, report_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id)
            row = report_for(con, project_id, report_id)
        return Response(json.dumps(row["upload"], ensure_ascii=False, allow_nan=False), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="breakroom-report-{row["id"]}.json"'})

    @app.post("/api/team/projects/{project_id}/compare")
    def compare(project_id: str, body: CompareInput, request: Request):
        from breakroom.reports import compare_reports
        with store.engine.connect() as con:
            authorize(request, con, project_id)
            baseline = report_for(con, project_id, body.baseline_id)["upload"]["report"]
            candidate = report_for(con, project_id, body.candidate_id)["upload"]["report"]
        return compare_reports([baseline], [candidate]) | {"provenance": "customer_generated"}

    @app.get("/api/team/projects/{project_id}/suites")
    def list_suites(project_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id, scope="suites:write")
            return {"items": [dict(row) for row in con.execute(select(suites).where(suites.c.project_id == project_id).limit(100)).mappings()]}

    @app.post("/api/team/projects/{project_id}/suites", status_code=201)
    def new_suite(project_id: str, body: SuiteInput, request: Request):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="developer", scope="suites:write")
            cases = [case.model_dump() for case in body.cases]
            if len({(case["case_id"], case["case_version"]) for case in cases}) != len(cases):
                raise HTTPException(422, "Suite contains duplicate case versions")
            if con.execute(select(func.count()).select_from(suites).where(suites.c.project_id == project_id)).scalar_one() >= 100:
                raise HTTPException(409, "Local beta suite limit reached")
            now = utcnow()
            row = {"id": opaque_id(), "project_id": project_id, "name": body.name, "cases": cases, "created_at": now, "updated_at": now}
            con.execute(insert(suites).values(**row))
            return row

    @app.delete("/api/team/projects/{project_id}/suites/{suite_id}")
    def delete_suite(project_id: str, suite_id: str, request: Request):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="developer", scope="suites:write")
            result = con.execute(delete(suites).where(suites.c.id == suite_id, suites.c.project_id == project_id))
            if not result.rowcount:
                raise HTTPException(404, "Private suite not found")
        return {"deleted": True, "id": suite_id}

    @app.post("/api/team/projects/{project_id}/retention/cleanup")
    def cleanup(project_id: str, request: Request):
        with store.engine.begin() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:manage")
            result = con.execute(delete(reports).where(reports.c.project_id == project_id, reports.c.expires_at <= utcnow()))
        return {"deleted_reports": result.rowcount}

    return app


app = create_app()
