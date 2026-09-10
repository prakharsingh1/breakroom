"""Only audited built-ins execute here. Customer adapters run on customer machines.

Run a single API process: rate limits, capacity and expiring reports are local
memory, deliberately separate from any future authenticated report service.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from breakroom.reports import SCHEMA_VERSION, compare_reports, export_case, validate_report
from breakroom.runner import run_case
from breakroom.scenarios import load_case, pack_directory


CASE_IDS = (
    "normal-refund",
    "refund-response-lost",
    "refund-before-commit",
    "ticket-write-failure",
    "similar-customers",
)
AGENTS = {
    "faulty": "breakroom.agents:faulty",
    "corrected": "breakroom.agents:corrected",
}
# Reviewed documentation catalog. Execution remains limited to CASE_IDS above.
CATALOG_IDS = CASE_IDS + (
    "idempotency-parameter-conflict", "distinct-same-amount-refunds", "concurrent-same-request",
    "pending-refund-succeeds", "pending-refund-fails", "temporary-rate-limit",
    "permanent-permission-denial", "permission-revoked-after-ticket-read", "ticket-version-conflict",
    "refund-exceeds-balance", "refund-currency-mismatch", "duplicate-request", "ambiguous-order",
    "incomplete-refund-response", "stale-refund-read", "duplicate-event-delivery",
    "reordered-events", "ticket-ownership-mismatch", "unresolved-before-deadline",
)
CaseID = Literal[
    "normal-refund",
    "refund-response-lost",
    "refund-before-commit",
    "ticket-write-failure",
    "similar-customers",
]


@dataclass(frozen=True)
class Settings:
    """Server-owned bounds; none can be supplied in public requests."""

    request_bytes: int = 4096
    report_bytes: int = 1_000_000
    export_bytes: int = 2_000_000
    max_reports: int = 200
    ttl_seconds: float = 1800
    run_timeout_seconds: float = 5
    runs_per_minute: int = 12
    requests_per_minute: int = 120
    max_rate_clients: int = 4096
    body_timeout_seconds: float = 5
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )

    def __post_init__(self) -> None:
        if not 0 < self.ttl_seconds <= 3600:
            raise ValueError("Demo retention must be between zero and one hour.")
        if not 0 < self.run_timeout_seconds <= 10:
            raise ValueError("Demo worker timeout must be between zero and ten seconds.")
        if not 0 < self.body_timeout_seconds <= 10:
            raise ValueError("Body read timeout must be between zero and ten seconds.")
        for field in (
            "request_bytes", "report_bytes", "export_bytes", "max_reports",
            "runs_per_minute", "requests_per_minute", "max_rate_clients",
        ):
            if getattr(self, field) <= 0:
                raise ValueError(f"{field} must be positive.")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent: Literal["faulty", "corrected"]
    case_id: CaseID


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    baseline_id: str = Field(min_length=32, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    candidate_id: str = Field(min_length=32, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def builtin_case(case_id: str) -> dict:
    if case_id not in CASE_IDS:
        raise HTTPException(422, "This Fire Drill is not available in the built-in demo.")
    try:
        # An explicit pack path avoids local load_case's deliberate support for
        # a user-selected path or a same-name file in the current directory.
        return load_case(pack_directory() / f"{case_id}.json")
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "The built-in Fire Drill is temporarily unavailable.") from exc


def catalog_case(case_id: str) -> dict:
    if case_id not in CATALOG_IDS:
        raise HTTPException(422, "This Fire Drill is not in the reviewed catalog.")
    try:
        manifest = load_case(pack_directory() / f"{case_id}.json")
        return {**manifest, "demo_available": case_id in CASE_IDS}
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "The Fire Drill contract is temporarily unavailable.") from exc


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


class DemoStore:
    def __init__(self, settings: Settings, clock: Callable[[], float]) -> None:
        self.settings = settings
        self.clock = clock
        self.lock = threading.Lock()
        self.worker = threading.BoundedSemaphore(1)
        self.reports: dict[str, tuple[float, bytes]] = {}
        self.rate: dict[str, tuple[deque[float], deque[float]]] = {}

    def _clean_locked(self, now: float) -> None:
        self.reports = {key: value for key, value in self.reports.items() if value[0] > now}
        for key, (requests, runs) in list(self.rate.items()):
            while requests and requests[0] <= now - 60:
                requests.popleft()
            while runs and runs[0] <= now - 60:
                runs.popleft()
            if not requests and not runs:
                del self.rate[key]

    def clean(self) -> None:
        with self.lock:
            self._clean_locked(self.clock())

    def check_rate(self, client: str, is_run: bool) -> int | None:
        with self.lock:
            now = self.clock()
            self._clean_locked(now)
            if client not in self.rate:
                if len(self.rate) >= self.settings.max_rate_clients:
                    return 503
                self.rate[client] = (deque(), deque())
            requests, runs = self.rate[client]
            if len(requests) >= self.settings.requests_per_minute:
                return 429
            requests.append(now)
            if is_run:
                if len(runs) >= self.settings.runs_per_minute:
                    return 429
                runs.append(now)
        return None

    def ensure_capacity(self) -> None:
        with self.lock:
            self._clean_locked(self.clock())
            if len(self.reports) >= self.settings.max_reports:
                raise HTTPException(503, "The demo report store is full. Try again after reports expire.")

    def save(self, agent: str, report: dict[str, Any]) -> dict[str, Any]:
        timestamp = time.time()
        run_id = secrets.token_urlsafe(24)
        envelope = {
            "id": run_id,
            "created_at": iso_timestamp(timestamp),
            "expires_at": iso_timestamp(timestamp + self.settings.ttl_seconds),
            "source": "live_builtin",
            "agent": agent,
            "report": report,
        }
        payload = encoded(envelope)
        if len(payload) > self.settings.report_bytes:
            raise HTTPException(502, "The demo worker exceeded the report size limit.")
        with self.lock:
            self._clean_locked(self.clock())
            if len(self.reports) >= self.settings.max_reports:
                raise HTTPException(503, "The demo report store is full. Try again later.")
            self.reports[run_id] = (self.clock() + self.settings.ttl_seconds, payload)
        return json.loads(payload)

    def get(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            self._clean_locked(self.clock())
            record = self.reports.get(run_id)
            if record is None:
                raise HTTPException(404, "This demo run is unknown or expired. Execute a new run.")
            # JSON copy keeps callers from changing previously saved evidence.
            return json.loads(record[1])


class RequestBounds:
    """Read only a bounded body, including requests without Content-Length."""

    def __init__(self, app: Any, store: DemoStore) -> None:
        self.app, self.store = app, store

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = self.store.settings

        async def reject(status: int, detail: str) -> None:
            headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            if status in (429, 503):
                headers["Retry-After"] = "60"
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)

        if len(scope.get("raw_path", b"")) > 2048:
            await reject(414, "Request path exceeds the demo limit.")
            return
        if scope["path"] != "/health":
            # Uvicorn is started with --no-proxy-headers. Do not trust a caller's
            # X-Forwarded-For to allocate a fresh rate-limit bucket.
            client = (scope.get("client") or ("unknown", 0))[0]
            status = self.store.check_rate(client, scope["method"] == "POST" and scope["path"] == "/api/runs")
            if status:
                await reject(status, "The shared demo is busy. Try again in a minute.")
                return
        headers = dict(scope.get("headers", []))
        if b"content-length" in headers:
            try:
                length = int(headers[b"content-length"])
            except ValueError:
                await reject(400, "Invalid Content-Length.")
                return
            if length < 0:
                await reject(400, "Invalid Content-Length.")
                return
            if length > settings.request_bytes:
                await reject(413, "Request body exceeds the demo limit.")
                return
        body = bytearray()
        body_deadline = time.monotonic() + settings.body_timeout_seconds
        while True:
            try:
                message = await asyncio.wait_for(receive(), timeout=max(0.001, body_deadline - time.monotonic()))
            except TimeoutError:
                await reject(408, "Request body was not received within the demo deadline.")
                return
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > settings.request_bytes:
                await reject(413, "Request body exceeds the demo limit.")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if body and headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() == b"application/json":
            try:
                json.loads(body, object_pairs_hook=unique_json_object, parse_constant=reject_json_constant)
            except (ValueError, UnicodeError, RecursionError):
                await reject(422, "Request must be valid bounded JSON without duplicate fields.")
                return
        sent_body = False

        async def bounded_receive() -> dict:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def safe_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                ]
            await send(message)

        await self.app(scope, bounded_receive, safe_send)


def create_app(settings: Settings | None = None, *, clock: Callable[[], float] = time.monotonic) -> FastAPI:
    settings = settings or Settings()
    store = DemoStore(settings, clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def expire_reports() -> None:
            while True:
                await asyncio.sleep(min(60, settings.ttl_seconds))
                store.clean()

        task = asyncio.create_task(expire_reports())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            with store.lock:
                store.reports.clear()
                store.rate.clear()

    app = FastAPI(title="Breakroom — Crash tests for AI agents", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.demo_store = store
    app.add_middleware(RequestBounds, store=store)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not reflect pasted source, credentials, or arbitrary values back in
        # default validation-error input fields.
        errors = [{"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/health")
    def health() -> dict:
        store.clean()
        for case_id in CASE_IDS:
            builtin_case(case_id)
        return {"status": "ok", "mode": "synthetic_builtin_demo", "report_schema_version": SCHEMA_VERSION}

    @app.get("/api/drills")
    def drills() -> dict:
        return {"drills": [catalog_case(case_id) for case_id in CATALOG_IDS]}

    @app.get("/api/drills/{case_id}")
    def drill(case_id: str) -> dict:
        return catalog_case(case_id)

    @app.post("/api/runs", status_code=201)
    def run(body: RunRequest) -> dict:
        if not store.worker.acquire(blocking=False):
            raise HTTPException(503, "The demo worker is busy. Try again shortly.", headers={"Retry-After": "2"})
        try:
            store.ensure_capacity()
            case = builtin_case(body.case_id)
            try:
                report = run_case(case, AGENTS[body.agent], seed=0, timeout=settings.run_timeout_seconds, allow_network=False, env_allowlist=())
                validate_report(report)
            except Exception as exc:
                # Keep worker internals and local paths out of public errors.
                raise HTTPException(502, "The demo worker could not produce valid evidence. Try a new run.") from exc
            return store.save(body.agent, report)
        finally:
            store.worker.release()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return store.get(run_id)

    @app.get("/api/runs/{run_id}/export")
    def get_export(run_id: str) -> Response:
        envelope = store.get(run_id)
        stream = BytesIO()
        with TemporaryDirectory(prefix="breakroom-export-") as temporary:
            directory = Path(export_case(envelope["report"], Path(temporary) / "regression"))
            with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
                total = 0
                # Fixed known files, never report-provided paths or recursive
                # directory traversal. Core export owns the runnable contents.
                for name in ("case.json", "test_regression.py", "README.md", "requirements.txt"):
                    path = directory / name
                    if not path.is_file() or path.is_symlink():
                        raise HTTPException(502, "The regression export is incomplete.")
                    size = path.stat().st_size
                    total += size
                    if total > settings.export_bytes:
                        raise HTTPException(502, "The regression export exceeds the demo limit.")
                    archive.writestr(f"breakroom-regression/{name}", path.read_bytes())
        payload = stream.getvalue()
        if len(payload) > settings.export_bytes:
            raise HTTPException(502, "The regression export exceeds the demo limit.")
        return Response(payload, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="breakroom-regression.zip"'})

    @app.post("/api/compare")
    def compare(body: CompareRequest) -> dict:
        baseline = store.get(body.baseline_id)["report"]
        candidate = store.get(body.candidate_id)["report"]
        result = compare_reports([baseline], [candidate])
        if not result.get("compatible", False):
            raise HTTPException(409, detail=result)
        return result

    return app


app = create_app()
