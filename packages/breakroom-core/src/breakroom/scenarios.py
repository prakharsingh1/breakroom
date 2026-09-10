"""Bounded JSON scenario manifests. Manifests contain data, never executable checks."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

MAX_CASE_BYTES = 262_144
ASSERTIONS = frozenset({"exact_amount", "correct_target", "no_duplicate_effects",
                        "ticket_consistent", "claims_supported", "authorized_writes",
                        "bounded_retries", "event_dedup"})
MANDATORY_ASSERTIONS = ASSERTIONS - {"event_dedup"}
TOOLS = frozenset({"find_customers", "get_customer", "get_order", "list_refunds",
                   "create_refund", "get_refund", "get_ticket", "append_note",
                   "update_ticket", "escalate", "receive_events", "ack_event"})
PHASE_ACTIONS = {
    "before": {"transient_error", "permission_denied", "rate_limit", "unavailable"},
    "after_commit": {"response_lost"},
    "read_response": {"incomplete", "stale"},
}


class ScenarioError(ValueError):
    pass


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _object(value: Any, required: set[str], optional: set[str], path: str) -> None:
    if not isinstance(value, dict):
        raise ScenarioError(f"{path}: expected object")
    missing, extra = required - value.keys(), value.keys() - required - optional
    if missing or extra:
        raise ScenarioError(f"{path}: missing {sorted(missing)}; unknown fields {sorted(extra)}")


def _text(value: Any, path: str, maximum: int = 4000) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ScenarioError(f"{path}: expected nonempty string up to {maximum} characters")


def _integer(value: Any, path: str, minimum: int = 0, maximum: int = 10**12) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ScenarioError(f"{path}: expected integer {minimum}..{maximum}")


def _bounded(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise ScenarioError("manifest nesting exceeds 12 levels")
    if isinstance(value, dict):
        if len(value) > 80 or any(not isinstance(k, str) or len(k) > 128 for k in value):
            raise ScenarioError("object exceeds bounds or contains non-string keys")
        for item in value.values():
            _bounded(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ScenarioError("array exceeds 128 items")
        for item in value:
            _bounded(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 16_384:
            raise ScenarioError("string exceeds 16384 characters")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ScenarioError("invalid Unicode surrogate in manifest")
    elif type(value) is int and abs(value) > 2**63 - 1:
        raise ScenarioError("integer exceeds signed 64-bit bounds")
    elif value is not None and type(value) not in (int, bool):
        raise ScenarioError("only JSON objects, arrays, strings, integers, booleans and null are allowed")


def _validate_case(case: dict) -> dict:
    _bounded(case)
    fields = {"schema_version", "case_id", "case_version", "pack_version", "number",
              "name", "summary", "tags", "severity", "provenance", "sources",
              "source_checked_at", "compatibility", "unsupported_behavior", "fixture_ref",
              "initial_state", "task", "faults", "required_capabilities", "allowed_outcomes",
              "liveness", "assertions", "negative_controls", "seed", "variation_constraints",
              "limitations", "reviewer_notes", "status"}
    _object(case, fields, set(), "case")
    if case["schema_version"] != "1.0":
        raise ScenarioError("unsupported scenario schema_version")
    if not isinstance(case["case_id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", case["case_id"]):
        raise ScenarioError("case_id: expected lowercase slug")
    for key in ("case_version", "pack_version"):
        if not isinstance(case[key], str) or not re.fullmatch(r"\d+\.\d+\.\d+", case[key]):
            raise ScenarioError(f"{key}: expected semantic version")
    for key in ("name", "summary", "compatibility", "fixture_ref", "reviewer_notes"):
        _text(case[key], key)
    if not isinstance(case["severity"], str) or case["severity"] not in {"low", "medium", "high", "critical"}:
        raise ScenarioError("invalid severity")
    if not isinstance(case["provenance"], str) or case["provenance"] not in {"synthetic", "documented_behavior", "sandbox_validated", "consented_case"}:
        raise ScenarioError("invalid provenance")
    if not isinstance(case["status"], str) or case["status"] not in {"implemented", "unsupported", "planned"}:
        raise ScenarioError("invalid implementation status")
    _integer(case["number"], "number", 1, 10000)
    _integer(case["seed"], "seed", 0, 2**32 - 1)
    for key in ("tags", "sources", "unsupported_behavior", "required_capabilities", "allowed_outcomes",
                "assertions", "negative_controls", "limitations"):
        if not isinstance(case[key], list) or any(not isinstance(x, str) or not x for x in case[key]):
            raise ScenarioError(f"{key}: expected list of strings")
        if len(case[key]) != len(set(case[key])):
            raise ScenarioError(f"{key}: duplicate values are not allowed")
    if not case["assertions"] or set(case["assertions"]) - ASSERTIONS:
        raise ScenarioError("assertions must name the allowlisted independent assertion registry")
    if not MANDATORY_ASSERTIONS <= set(case["assertions"]):
        raise ScenarioError("support-refund cases must retain every mandatory safety, outcome and evidence assertion")
    if not case["allowed_outcomes"] or set(case["allowed_outcomes"]) - {"completed", "escalated", "pending"}:
        raise ScenarioError("invalid allowed_outcomes")
    if case["source_checked_at"] is not None:
        if not isinstance(case["source_checked_at"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", case["source_checked_at"]):
            raise ScenarioError("source_checked_at: expected ISO date or null")
        try:
            date.fromisoformat(case["source_checked_at"])
        except ValueError as exc:
            raise ScenarioError("source_checked_at: invalid calendar date") from exc
    if case["provenance"] in {"documented_behavior", "sandbox_validated"} and (not case["sources"] or case["source_checked_at"] is None):
        raise ScenarioError("documented and sandbox provenance require sources and source-check date")
    _object(case["variation_constraints"], {"description", "supported_seeds"}, set(), "variation_constraints")
    _text(case["variation_constraints"]["description"], "variation description")
    if not isinstance(case["variation_constraints"]["supported_seeds"], list) or not case["variation_constraints"]["supported_seeds"]:
        raise ScenarioError("supported_seeds: expected a nonempty integer array")
    for seed in case["variation_constraints"]["supported_seeds"]:
        _integer(seed, "supported seed", 0, 2**32 - 1)
    if case["seed"] not in case["variation_constraints"]["supported_seeds"]:
        raise ScenarioError("default seed must be a supported seed")
    _object(case["liveness"], {"deadline_seconds", "max_tool_calls", "max_refund_attempts"}, set(), "liveness")
    for key, value in case["liveness"].items():
        _integer(value, f"liveness.{key}", 1, 1000)
    task = case["task"]
    _object(task, {"request_id", "requester", "customer_id", "order_id", "ticket_id", "amount_minor",
                   "currency", "text", "policy"}, set(), "task")
    for key in ("request_id", "text", "currency"):
        _text(task[key], "task." + key)
    if not re.fullmatch(r"[A-Z]{3}", task["currency"]):
        raise ScenarioError("task.currency: expected explicit ISO-style currency")
    _integer(task["amount_minor"], "task.amount_minor", 1)
    for key in ("customer_id", "order_id", "ticket_id"):
        if task[key] is not None:
            _text(task[key], "task." + key, 120)
    _object(task["requester"], {"customer_id", "tenant_id", "contact_ref", "display_name", "authenticated"}, set(), "requester")
    for key in ("customer_id", "tenant_id", "contact_ref", "display_name"):
        _text(task["requester"][key], "requester." + key, 200)
    if type(task["requester"]["authenticated"]) is not bool:
        raise ScenarioError("requester.authenticated must be a boolean")
    _object(task["policy"], {"max_attempts", "refund_authorized", "require_ticket", "allow_escalation"}, set(), "policy")
    _integer(task["policy"]["max_attempts"], "max_attempts", 1, 20)
    for key in ("refund_authorized", "require_ticket", "allow_escalation"):
        if type(task["policy"][key]) is not bool:
            raise ScenarioError(f"policy.{key}: expected boolean")
    initial = case["initial_state"]
    _object(initial, {"customers", "orders", "refunds", "tickets", "permissions"}, set(), "initial_state")
    records = {
        "customers": {"id", "display_name", "contact_ref", "tenant_id"},
        "orders": {"id", "customer_id", "amount_minor", "currency", "status"},
        "refunds": {"id", "order_id", "amount_minor", "currency", "logical_request_id", "operation_key", "status", "created_at"},
        "tickets": {"id", "customer_id", "order_id", "version", "status", "messages"},
        "permissions": {"operation", "allowed"},
    }
    for collection, keys in records.items():
        if not isinstance(initial[collection], list):
            raise ScenarioError(f"{collection}: expected array")
        ids = set()
        for row in initial[collection]:
            _object(row, keys, set(), collection)
            for key in (keys - {"amount_minor", "version", "created_at", "messages", "allowed"}):
                _text(row[key], f"{collection}.{key}")
            if "id" in row:
                if row["id"] in ids:
                    raise ScenarioError(f"{collection}: duplicate record ID")
                ids.add(row["id"])
            if "amount_minor" in row:
                _integer(row["amount_minor"], f"{collection}.amount_minor", 1)
                if not re.fullmatch(r"[A-Z]{3}", row["currency"]):
                    raise ScenarioError("record currency must be explicit")
            if "version" in row:
                _integer(row["version"], "ticket.version", 1)
                if row["messages"] != []:
                    raise ScenarioError("initial ticket messages must currently be empty")
                if row["status"] not in {"open", "resolved", "escalated"}:
                    raise ScenarioError("invalid initial ticket status")
            if "created_at" in row:
                _integer(row["created_at"], "refund.created_at", 0, 10**9)
            if "allowed" in row and type(row["allowed"]) is not bool:
                raise ScenarioError("permission allowed must be boolean")
    customers = {c["id"] for c in initial["customers"]}
    if not customers:
        raise ScenarioError("fixture must contain a customer")
    orders = {o["id"]: o for o in initial["orders"]}
    for order in orders.values():
        if order["customer_id"] not in customers or order["status"] != "paid":
            raise ScenarioError("orders must belong to fixture customers and be paid")
    if not orders:
        raise ScenarioError("fixture must contain an order")
    permission_names = [p["operation"] for p in initial["permissions"]]
    if set(permission_names) != {"refund", "ticket_write"} or len(permission_names) != 2:
        raise ScenarioError("fixture requires unique refund and ticket_write permissions")
    operation_keys = [r["operation_key"] for r in initial["refunds"]]
    if len(set(operation_keys)) != len(operation_keys):
        raise ScenarioError("initial refunds contain duplicate operation keys")
    for ticket in initial["tickets"]:
        if ticket["customer_id"] not in customers or ticket["order_id"] not in orders:
            raise ScenarioError("ticket references an unknown customer/order")
    for refund in initial["refunds"]:
        if refund["order_id"] not in orders or refund["status"] not in {"pending", "succeeded", "failed"}:
            raise ScenarioError("invalid fixture refund")
        if refund["currency"] != orders[refund["order_id"]]["currency"]:
            raise ScenarioError("fixture refund currency mismatch")
    for order_id, order in orders.items():
        reserved = sum(r["amount_minor"] for r in initial["refunds"] if r["order_id"] == order_id and r["status"] != "failed")
        if reserved > order["amount_minor"]:
            raise ScenarioError("fixture refunds exceed paid balance")
    if not isinstance(case["faults"], list) or len(case["faults"]) > 32:
        raise ScenarioError("faults: expected array with at most 32 faults")
    fault_ids = set()
    for fault in case["faults"]:
        _object(fault, {"id", "tool", "invocation", "phase", "action", "expected"}, {"retry_after"}, "fault")
        _text(fault["id"], "fault.id", 80)
        if fault["id"] in fault_ids:
            raise ScenarioError("duplicate fault ID")
        fault_ids.add(fault["id"])
        if any(not isinstance(fault[key], str) for key in ("tool", "phase", "action")) or fault["tool"] not in TOOLS or fault["action"] not in PHASE_ACTIONS.get(fault["phase"], set()):
            raise ScenarioError("fault tool/phase/action is not implemented")
        if fault["phase"] == "after_commit" and fault["tool"] not in {"create_refund", "append_note", "update_ticket"}:
            raise ScenarioError("after_commit requires a write operation")
        _integer(fault["invocation"], "fault.invocation", 1, 1000)
        if type(fault["expected"]) is not bool:
            raise ScenarioError("fault.expected: expected boolean")
        if "retry_after" in fault:
            _integer(fault["retry_after"], "fault.retry_after", 0, 300)
    if len(json.dumps(case).encode()) > MAX_CASE_BYTES:
        raise ScenarioError("case exceeds maximum byte size")
    return copy.deepcopy(case)


def validate_case(case: dict) -> dict:
    """Every malformed data shape produces a useful scenario configuration error."""
    try:
        if isinstance(case, dict) and case.get("schema_version") == "2.0":
            return _validate_v2(case)
        return _validate_case(case)
    except ScenarioError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ScenarioError(f"Invalid scenario data: {exc}") from exc


BEHAVIOR_DEFAULTS = {
    "refund_initial_status": "succeeded", "terminal_status": None, "transition_delay": 2,
    "rate_limit_until": 0, "hidden_until": 0, "stale_until": 0,
    "emit_events": False, "event_delivery": "normal",
    "revoke_after_ticket_read": False, "edit_after_ticket_read": False,
}
OBSERVATIONS = frozenset({"idempotency_conflict", "sequential_dispatch", "concurrent_barrier",
    "pending_observed", "terminal_succeeded", "terminal_failed", "rate_limited",
    "permission_denied", "permission_revoked", "version_conflict", "balance_refused",
    "currency_observed", "incomplete_response", "stale_read", "event_duplicate_received",
    "event_reordered_received", "ticket_mismatch_observed", "ambiguous_order_observed", "hidden_response"})


def _validate_v2(case: dict) -> dict:
    _bounded(case)
    extras = {"execution_plan", "behavior", "expectations"}
    if not extras <= case.keys():
        raise ScenarioError("scenario 2.0 requires execution_plan, behavior and expectations")
    legacy = {key: copy.deepcopy(value) for key, value in case.items() if key not in extras}
    legacy["schema_version"] = "1.0"
    _validate_case(legacy)
    plan = case["execution_plan"]
    _object(plan, {"mode", "tasks"}, set(), "execution_plan")
    if not isinstance(plan["mode"], str) or plan["mode"] not in {"single", "sequential", "concurrent"}:
        raise ScenarioError("invalid execution_plan.mode")
    if not isinstance(plan["tasks"], list) or not 1 <= len(plan["tasks"]) <= 8:
        raise ScenarioError("execution_plan.tasks requires 1..8 task envelopes")
    if plan["mode"] == "single" and len(plan["tasks"]) != 1:
        raise ScenarioError("single mode requires exactly one task")
    if plan["mode"] == "concurrent" and not 2 <= len(plan["tasks"]) <= 4:
        raise ScenarioError("concurrent mode requires 2..4 handlers")
    if plan["tasks"][0] != case["task"]:
        raise ScenarioError("the first execution task must equal the primary task")
    for task in plan["tasks"]:
        item = copy.deepcopy(legacy)
        item["task"] = task
        _validate_case(item)
        if task["requester"]["tenant_id"] != case["task"]["requester"]["tenant_id"]:
            raise ScenarioError("one trial invocation plan must stay within one tenant")
    authorizations = {}
    for task in plan["tasks"]:
        authorization = {key: task[key] for key in ("requester", "customer_id", "order_id", "ticket_id", "amount_minor", "currency", "policy")}
        if task["request_id"] in authorizations and authorizations[task["request_id"]] != authorization:
            raise ScenarioError("duplicate deliveries of a logical request cannot change its authorization")
        authorizations[task["request_id"]] = authorization
    behavior = case["behavior"]
    _object(behavior, set(BEHAVIOR_DEFAULTS), set(), "behavior")
    if behavior["refund_initial_status"] not in {"pending", "succeeded"} or behavior["terminal_status"] not in {None, "succeeded", "failed"}:
        raise ScenarioError("unsupported refund lifecycle state")
    if behavior["refund_initial_status"] == "pending" and behavior["terminal_status"] is None:
        raise ScenarioError("pending scenarios require a terminal transition")
    for key in ("transition_delay", "rate_limit_until", "hidden_until", "stale_until"):
        _integer(behavior[key], "behavior." + key, 0, 300)
    for key in ("emit_events", "revoke_after_ticket_read", "edit_after_ticket_read"):
        if type(behavior[key]) is not bool:
            raise ScenarioError("behavior." + key + " must be boolean")
    if behavior["event_delivery"] not in {"normal", "duplicate", "reordered"}:
        raise ScenarioError("invalid event delivery order")
    if behavior["emit_events"] and len(plan["tasks"]) != 1:
        raise ScenarioError("the current event adapter supports one request per event trial")
    expect = case["expectations"]
    _object(expect, {"forbid_new_effects", "preserve_concurrent_edits", "observations"}, set(), "expectations")
    for key in ("forbid_new_effects", "preserve_concurrent_edits"):
        if type(expect[key]) is not bool:
            raise ScenarioError("expectations." + key + " must be boolean")
    if not isinstance(expect["observations"], list) or any(not isinstance(x, str) or x not in OBSERVATIONS for x in expect["observations"]):
        raise ScenarioError("unknown required coverage observation")
    if len(json.dumps(case).encode()) > MAX_CASE_BYTES:
        raise ScenarioError("case exceeds maximum byte size")
    return copy.deepcopy(case)


def _json_no_duplicates(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_case(case_id: str | Path) -> dict:
    selected = Path(case_id)
    if not selected.is_file():
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", case_id):
            raise ScenarioError("unknown case or invalid path")
        selected = pack_directory() / f"{case_id}.json"
    try:
        if selected.stat().st_size > MAX_CASE_BYTES:
            raise ScenarioError("case exceeds maximum byte size")
        case = json.loads(selected.read_text(), object_pairs_hook=_json_no_duplicates)
    except ScenarioError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ScenarioError(f"Cannot load JSON case {case_id}: {exc}") from exc
    return validate_case(case)


def pack_directory() -> Path:
    bundled = Path(__file__).parent / "data" / "support-refunds"
    if bundled.is_dir():
        return bundled
    root = Path(__file__).resolve().parents[4]
    return root / "scenario-packs" / "support-refunds"


def load_pack(path: str | Path) -> list[dict]:
    folder = Path(path)
    files = sorted(folder.glob("*.json"))
    if not files or len(files) > 128:
        raise ScenarioError("pack must contain 1..128 JSON cases")
    cases = [load_case(file) for file in files]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ScenarioError("pack has duplicate case IDs")
    return sorted(cases, key=lambda case: case["number"])


def list_cases(pack: str = "support-refunds") -> list[dict]:
    if pack != "support-refunds":
        return load_pack(pack)
    return load_pack(pack_directory())
