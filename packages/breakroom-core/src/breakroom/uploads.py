"""Explicit, minimized customer-generated report sharing. No run uploads itself."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .reports import read_json, validate_report
from .scenarios import ASSERTIONS, OBSERVATIONS, PHASE_ACTIONS, TOOLS, content_hash, validate_case

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
UPLOAD_FORMAT = "breakroom-minimized-v1"
NOTICE = "Customer-generated, locally minimized evidence. Original hashes are customer-supplied provenance, not independent certification. Automated minimization is imperfect; review before uploading."
REDACTED = "Removed during local minimization."
CHECK_MESSAGE = "Check explanation removed during local minimization; status is a customer-generated assertion."
NORMALIZATION = "redacted_materialized_snapshot"
_OPAQUE = re.compile(r"anon-[0-9a-f]{32}\Z")
_CONTACT = re.compile(r"anon-[0-9a-f]{32}@example\.invalid\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}(?:\.[0-9]{1,4})?\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")

_BUILTIN_CASE_IDS = frozenset({
    "normal-refund", "refund-response-lost", "refund-before-commit", "ticket-write-failure", "similar-customers",
    "idempotency-parameter-conflict", "distinct-same-amount-refunds", "concurrent-same-request", "pending-refund-succeeds",
    "pending-refund-fails", "temporary-rate-limit", "permanent-permission-denial", "permission-revoked-after-ticket-read",
    "ticket-version-conflict", "refund-exceeds-balance", "refund-currency-mismatch", "duplicate-request", "ambiguous-order",
    "incomplete-refund-response", "stale-refund-read", "duplicate-event-delivery", "reordered-events",
    "ticket-ownership-mismatch", "unresolved-before-deadline",
})
_ENUMS = frozenset({
    "breakroom", "pass", "fail", "unknown", "not_applicable", "PASS", "FAIL", "INCONCLUSIVE", "UNSUPPORTED",
    "completed", "errored", "timed_out", "cancelled", "pending", "succeeded", "failed", "paid", "open", "resolved",
    "escalated", "acknowledged", "synthetic", "documented_behavior", "sandbox_validated", "consented_case",
    "implemented", "unsupported", "planned", "low", "medium", "high", "critical", "safety", "outcome", "evidence",
    "coverage", "execution", "liveness", "refund", "ticket_write", "single", "sequential", "concurrent", "normal",
    "duplicate", "reordered", "before", "after_commit", "read_response", "refund_succeeded", "refund_pending",
    "refund_failed", "ticket_updated", "system", "refund_transition", "payments", "tickets", "reconciliation",
    "virtual_clock", "events", "event_deduplication", "concurrency", "idempotency_conflict", "permission_denied",
    "insufficient_refundable_balance", "rate_limit", "transport_timeout", "version_conflict", "transient_error",
    "adapter_capabilities", "fault_coverage", "execution_completed", "logical_deadline", "amount_authorization",
    "ticket_target", "required_outcome_claim", "claims_supported", "tool_call", "tool_response", "tool_error",
    "refund_committed", "ticket_note_committed", "ticket_status_committed", "fault_triggered", "clock_advanced",
    "escalation_requested", "invocation_started", "invocation_completed", "barrier_arrived",
    "concurrency_barrier_registered", "currency_observed", "event_delivered", "event_acknowledged",
}) | frozenset(ASSERTIONS) | frozenset(OBSERVATIONS) | frozenset(TOOLS) | frozenset(PHASE_ACTIONS) | frozenset(action for values in PHASE_ACTIONS.values() for action in values)
_IDENTIFIER_FIELDS = frozenset({
    "id", "run_id", "customer_id", "order_id", "ticket_id", "request_id", "logical_request_id", "operation_id",
    "operation_key", "tenant_id", "refund_id", "event_id", "fault_id", "handler_id", "evidence_ref",
    "consumer_operation_id", "evidence_refs", "order_ids", "reference",
})
_TEXT_FIELDS = frozenset({
    "name", "summary", "text", "customer_text", "message", "reason", "compatibility", "reviewer_notes", "description",
    "fixture_ref", "limitations", "unsupported_behavior",
})
_SAFE_DATA_FIELDS = frozenset({
    "id", "customer_id", "order_id", "ticket_id", "request_id", "logical_request_id", "operation_id", "operation_key",
    "tenant_id", "refund_id", "event_id", "fault_id", "handler_id", "evidence_ref", "evidence_refs", "consumer_operation_id",
    "amount_minor", "currency", "status", "version", "expected_version", "created_at", "returned_at", "retry_after",
    "retry_at", "remaining_minor", "permission", "permission_allowed", "allowed", "operation", "phase", "action", "code",
    "kind", "type", "tool", "invocation", "seq", "at", "seconds", "handlers", "last_event_seq", "delivery_id", "actor",
    "refund", "ticket", "order", "customer", "response", "items", "event", "event_kind", "capabilities", "limit",
})


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _opaque(value: str, key: bytes) -> str:
    if _OPAQUE.fullmatch(value):
        return value
    return "anon-" + hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _scrub(value: object, key: bytes, field: str = "") -> object:
    if isinstance(value, dict):
        return {name: _scrub(child, key, name) for name, child in value.items()}
    if isinstance(value, list):
        return [_scrub(child, key, field) for child in value]
    if not isinstance(value, str):
        return value
    if field in _TEXT_FIELDS:
        return REDACTED
    if field == "display_name":
        return "Redacted customer"
    if field == "contact_ref":
        return value if _CONTACT.fullmatch(value) else _opaque(value, key) + "@example.invalid"
    if field == "case_id":
        return value if value in _BUILTIN_CASE_IDS else _opaque(value, key)
    if field in _IDENTIFIER_FIELDS:
        return _opaque(value, key)
    if field in {"schema_version", "engine_version", "oracle_version", "case_version", "pack_version"}:
        if not _VERSION.fullmatch(value):
            raise ValueError("Minimized uploads support numeric contract versions only.")
        return value
    if field == "currency" and re.fullmatch(r"[A-Z]{3}", value):
        return value
    return value if value in _ENUMS else _opaque(value, key)


def _safe_data(value: object, key: bytes, field: str = "") -> object:
    if isinstance(value, dict):
        return {name: _safe_data(child, key, name) for name, child in value.items() if name in _SAFE_DATA_FIELDS}
    if isinstance(value, list):
        return [_safe_data(child, key, field) for child in value]
    return _scrub(value, key, field)


def _manifest(source: dict, key: bytes) -> dict:
    # Scenario validation is the path-specific structural allowlist. It rejects
    # unknown keys, executable assertion strings, and unbounded contributor data.
    manifest = _scrub(validate_case(source), key)
    manifest["name"] = REDACTED
    manifest["sources"] = [REDACTED] if source["sources"] else []
    manifest["source_checked_at"] = source["source_checked_at"]
    manifest["tags"] = []
    manifest["negative_controls"] = []
    manifest["limitations"] = [REDACTED]
    manifest["unsupported_behavior"] = [REDACTED]
    # A sharing copy is an already materialized snapshot. Do not claim that the
    # original generator would reproduce transformed names/text/identifiers.
    manifest["variation_constraints"] = {"description": REDACTED, "supported_seeds": [source["seed"]]}
    return validate_case(manifest)


def _state(state: dict, key: bytes) -> dict:
    result = {}
    row_fields = {
        "customers": {"id", "display_name", "contact_ref", "tenant_id"},
        "orders": {"id", "customer_id", "amount_minor", "currency", "status"},
        "refunds": {"id", "order_id", "amount_minor", "currency", "logical_request_id", "operation_key", "status", "created_at"},
        "tickets": {"id", "customer_id", "order_id", "version", "status", "messages"},
        "permissions": {"operation", "allowed"},
        "fault_counters": {"id", "triggered"},
    }
    if "available" in state:
        if type(state["available"]) is not bool:
            raise ValueError("Unsupported state availability marker.")
        result["available"] = state["available"]
    if "reason" in state:
        result["reason"] = None if state["reason"] is None else REDACTED
    for collection, fields in row_fields.items():
        if collection in state:
            if not isinstance(state[collection], list):
                raise ValueError("Unsupported state collection in minimized report.")
            rows = []
            for row in state[collection]:
                if not isinstance(row, dict):
                    raise ValueError("Unsupported state record in minimized report.")
                kept = {name: value for name, value in row.items() if name in fields}
                if "messages" in kept:
                    if not isinstance(kept["messages"], list):
                        raise ValueError("Unsupported ticket messages.")
                    kept["messages"] = [_safe_data(message, key) for message in kept["messages"]]
                if any(isinstance(value, (dict, list)) for name, value in kept.items() if name != "messages"):
                    raise ValueError("Unsupported structured state field.")
                rows.append(_scrub(kept, key))
            result[collection] = rows
    if "clock" in state:
        if state["clock"] is not None and type(state["clock"]) not in (int, float):
            raise ValueError("Unsupported state clock.")
        result["clock"] = state["clock"]
    return result


def _result(source: object, key: bytes) -> dict | None:
    if not isinstance(source, dict):
        return None
    claims, refs = source.get("claims"), source.get("evidence_refs")
    result = {"claims": [_safe_data(claim, key) for claim in claims] if isinstance(claims, list) else None,
              "evidence_refs": [_opaque(ref, key) for ref in refs] if isinstance(refs, list) and all(isinstance(ref, str) for ref in refs) else None,
              "escalated": source.get("escalated") if type(source.get("escalated")) is bool else None,
              "customer_text": REDACTED}
    if isinstance(source.get("invocations"), list):
        result["invocations"] = []
        for invocation in source["invocations"]:
            if not isinstance(invocation, dict):
                raise ValueError("Unsupported invocation evidence.")
            safe = _safe_data({name: value for name, value in invocation.items() if name != "result"}, key)
            safe["result"] = _result(invocation.get("result"), key)
            result["invocations"].append(safe)
    return result


def _minimize_report(report: dict, key: bytes) -> dict:
    validate_report(report)
    manifest = _manifest(report["case"]["manifest"], key)
    checks = []
    for check in report["checks"]:
        identifier = check["id"] if check["id"] in _ENUMS else _opaque(check["id"], key)
        checks.append({"id": identifier, "category": _scrub(check["category"], key), "status": check["status"],
                       "message": CHECK_MESSAGE, "evidence_refs": [_opaque(ref, key) for ref in check["evidence_refs"]]})
    metrics = {}
    for name in ("tool_calls", "refund_count", "successful_refund_count"):
        if name in report["metrics"]:
            value = report["metrics"][name]
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Unsupported metrics in minimized report.")
            metrics[name] = value
    totals = report["metrics"].get("refunded_minor_by_currency")
    if isinstance(totals, dict):
        if any(not re.fullmatch(r"[A-Z]{3}", currency) or type(amount) is not int for currency, amount in totals.items()):
            raise ValueError("Unsupported currency totals in minimized report.")
        metrics["refunded_minor_by_currency"] = totals.copy()
    elif totals is None:
        metrics["refunded_minor_by_currency"] = None
    metrics.update(tokens=None, cost=None)
    execution = {"status": report["execution"]["status"], "error": None if report["execution"].get("error") is None else REDACTED}
    for name in ("duration_ms", "wall_duration_ms"):
        if name in report["execution"]:
            value = report["execution"][name]
            if value is not None and (type(value) not in (int, float) or value < 0):
                raise ValueError("Unsupported execution timing.")
            execution[name] = value
    minimized = {
        "schema_version": report["schema_version"], "producer": "breakroom",
        "engine_version": _scrub(report["engine_version"], key, "engine_version"),
        "oracle_version": _scrub(report["oracle_version"], key, "oracle_version"),
        "run_id": _opaque(report["run_id"], key),
        "case": {"case_id": manifest["case_id"], "case_version": manifest["case_version"], "pack_version": manifest["pack_version"],
                 "manifest": manifest, "manifest_hash": content_hash(manifest), "fixture_hash": content_hash(manifest["initial_state"])},
        "agent": {"reference": _opaque(str(report["agent"].get("reference", "unreported")), key), "adapter_version": None, "code_hash": None},
        "seed": report["seed"], "execution": execution, "initial_state": manifest["initial_state"],
        "events": [{name: _safe_data(value, key, name) for name, value in event.items() if name in {"id", "seq", "at", "kind", "tool", "data"}} for event in report["events"]],
        "final_state": _state(report["final_state"], key), "checks": checks, "verdict": report["verdict"],
        "limitations": [NOTICE, "Detailed free text, tool payloads and execution provenance were minimized; this sharing copy is not a faithful executable regression export."],
        "metrics": metrics,
    }
    if "agent_result" in report:
        minimized["agent_result"] = _result(report["agent_result"], key)
    return validate_report(minimized)


def prepare_upload(report: dict, *, redaction_key: bytes) -> dict:
    if not isinstance(redaction_key, bytes) or len(redaction_key) != 32:
        raise ValueError("A local 32-byte redaction key is required.")
    validate_report(report)
    original = report["case"]
    payload = {
        "schema_version": "1.0", "kind": "customer_generated_report", "report": _minimize_report(report, redaction_key),
        "privacy": {"format": UPLOAD_FORMAT, "normalization": NORMALIZATION,
                    "original_report_sha256": hashlib.sha256(_canonical(report)).hexdigest(),
                    "original_case_manifest_hash": original["manifest_hash"], "original_fixture_hash": original["fixture_hash"],
                    "original_source_manifest_hash": original.get("source_manifest_hash"),
                    "original_generator_version": original.get("generator_version"), "notice": NOTICE},
    }
    return validate_upload_envelope(payload)


def validate_upload_envelope(payload: dict) -> dict:
    """Server-side validation rechecks every allowed value, not a redaction flag."""
    try:
        if len(_canonical(payload)) > MAX_UPLOAD_BYTES:
            raise ValueError("Prepared report exceeds the 2 MiB upload limit.")
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "kind", "report", "privacy"}:
            raise ValueError("Unsupported upload envelope fields.")
        if payload["schema_version"] != "1.0" or payload["kind"] != "customer_generated_report":
            raise ValueError("Unsupported upload envelope version or kind.")
        privacy = payload["privacy"]
        fields = {"format", "normalization", "original_report_sha256", "original_case_manifest_hash", "original_fixture_hash", "original_source_manifest_hash", "original_generator_version", "notice"}
        if not isinstance(privacy, dict) or set(privacy) != fields or privacy["format"] != UPLOAD_FORMAT or privacy["normalization"] != NORMALIZATION or privacy["notice"] != NOTICE:
            raise ValueError("Unsupported minimization/provenance fields.")
        for name in ("original_report_sha256", "original_case_manifest_hash", "original_fixture_hash"):
            if not isinstance(privacy[name], str) or not _HASH.fullmatch(privacy[name]):
                raise ValueError("Invalid original provenance checksum.")
        if privacy["original_source_manifest_hash"] is not None and (not isinstance(privacy["original_source_manifest_hash"], str) or not _HASH.fullmatch(privacy["original_source_manifest_hash"])):
            raise ValueError("Invalid original source checksum.")
        if privacy["original_generator_version"] is not None and (not isinstance(privacy["original_generator_version"], str) or not _VERSION.fullmatch(privacy["original_generator_version"])):
            raise ValueError("Invalid original generator version.")
        if (privacy["original_source_manifest_hash"] is None) != (privacy["original_generator_version"] is None):
            raise ValueError("Original source and generator provenance must be supplied together.")
        report = validate_report(payload["report"])
        # Already opaque IDs and fixed replacement text are stable under a
        # second minimization with an unrelated key. Raw fields change/remove,
        # causing strict rejection. No server-side secret or source URL needed.
        if _minimize_report(report, b"\x00" * 32) != report:
            raise ValueError("Report is not in the supported minimized format. Prepare it locally before upload.")
        return payload
    except (TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("Malformed bounded report upload.") from exc


def upload_digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(validate_upload_envelope(payload))).hexdigest()


def load_redaction_key(path: str | Path) -> bytes:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if destination.is_symlink():
            raise ValueError("Redaction-key file must not be a symlink.")
    else:
        with os.fdopen(descriptor, "wb") as target:
            target.write(secrets.token_bytes(32))
    if destination.stat().st_size != 32:
        raise ValueError("Redaction-key file must contain exactly 32 bytes.")
    return destination.read_bytes()


def save_prepared_upload(payload: dict, out: str | Path) -> Path:
    validate_upload_envelope(payload)
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise ValueError("Pretty-printed prepared report exceeds the 2 MiB upload limit.")
    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return destination


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _server_url(server: str, project_id: str, allow_localhost_http: bool) -> str:
    if not isinstance(server, str) or any(ord(character) <= 32 or ord(character) == 127 for character in server):
        raise ValueError("Server must be an origin without whitespace or control characters.")
    parsed = urlsplit(server)
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Server must be an origin without credentials, path, query, or fragment.")
    if not parsed.hostname or parsed.scheme not in {"https", "http"}:
        raise ValueError("An explicit HTTPS server origin is required.")
    if parsed.scheme == "http" and not (allow_localhost_http and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("HTTP is allowed only for an explicitly opted-in localhost development server.")
    if not _ID.fullmatch(project_id):
        raise ValueError("Invalid project identifier.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid server port.") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/team/projects/" + quote(project_id, safe="") + "/reports", "", ""))


def upload_prepared_file(path: str | Path, *, server: str, project_id: str,
                         token_env: str = "BREAKROOM_API_TOKEN", allow_localhost_http: bool = False,
                         expected_sha256: str | None = None) -> dict:
    """The sole network operation: explicitly send one locally reviewed file."""
    url = _server_url(server, project_id, allow_localhost_http)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", token_env):
        raise ValueError("Token must be named by a valid environment variable.")
    token = os.environ.get(token_env)
    if not token or len(token) > 8192 or any(not 33 <= ord(character) <= 126 for character in token):
        raise ValueError("The configured token environment variable is absent or invalid.")
    payload = validate_upload_envelope(read_json(path, max_bytes=MAX_UPLOAD_BYTES))
    digest = upload_digest(payload)
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256) or not hmac.compare_digest(digest, expected_sha256)):
        raise ValueError("Prepared report does not match the explicitly reviewed canonical checksum.")
    request = Request(url, data=_canonical(payload), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "Authorization": "Bearer " + token,
        "Idempotency-Key": digest,
    })
    try:
        # Avoid inherited proxy routing. TLS certificate verification remains
        # the standard library default; redirects never forward credentials.
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=10) as response:
            if response.status not in {200, 201}:
                raise ValueError(f"Report upload returned HTTP {response.status}.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Upload response exceeded the 64 KiB limit.")
            result = json.loads(body)
            if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not _ID.fullmatch(result["id"]) or result.get("project_id") != project_id or result.get("provenance") != "customer_generated" or type(result.get("duplicate")) is not bool:
                raise ValueError("Server returned an invalid upload acknowledgment.")
            if token in result["id"]:
                raise ValueError("Server returned an invalid upload acknowledgment.")
            return {"id": result["id"], "project_id": project_id, "duplicate": result["duplicate"], "status": response.status}
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise ValueError(f"Report upload rejected with HTTP {status}; no redirects are followed.") from None
    except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Report upload did not receive a valid bounded response. Review the server and retry the same prepared file if appropriate.") from None
