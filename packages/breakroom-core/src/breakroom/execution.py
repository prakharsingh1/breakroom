"""Run a bounded invocation plan inside one isolated trial worker.

Each invocation receives its own adapter context and tool facade. Concurrency
uses real threads and the simulator's per-operation SQLite connections; no lock
serializes adapter execution. The containing worker provides hard termination
for adapters which do not cooperate with their shared deadline/cancellation.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .models import AgentCancelled, AgentContext, AgentResult, BudgetExceeded, SupportTools, TaskEnvelope
from .simulator import Simulator


def _structured_result(returned: object) -> tuple[dict | None, str | None]:
    """Malformed adapter output is unknown evidence, even after a normal return."""
    if not isinstance(returned, AgentResult):
        return None, "Adapter returned no parseable AgentResult; evidence remains unknown"
    malformed = "Adapter returned malformed structured claims; evidence remains unknown"
    try:
        candidate = dataclasses.asdict(returned)
        if (not isinstance(candidate["claims"], list) or len(candidate["claims"]) > 64
                or any(not isinstance(claim, dict) for claim in candidate["claims"])
                or any(any(not isinstance(key, str) or len(key) > 100
                           or (value is not None and type(value) not in (str, int, bool))
                           or (isinstance(value, str) and len(value) > 4000)
                           or (key == "amount_minor" and type(value) is not int)
                           for key, value in claim.items()) for claim in candidate["claims"])
                or not isinstance(candidate["evidence_refs"], list) or len(candidate["evidence_refs"]) > 128
                or any(not isinstance(ref, str) or len(ref) > 200 for ref in candidate["evidence_refs"])
                or type(candidate["escalated"]) is not bool
                or not isinstance(candidate["customer_text"], str)
                or len(candidate["customer_text"]) > 16384):
            return None, malformed
        if len(json.dumps(candidate, allow_nan=False).encode()) > 65536:
            return None, "Adapter result exceeds the 65536-byte evidence limit"
        return candidate, None
    except Exception:
        return None, malformed


def execute_invocations(case: dict, agent: Callable, simulator: Simulator, *,
                        deadline_seconds: float, cancel_signal=None) -> tuple[list[dict], dict]:
    """Execute an already validated schema-2 plan and persist every completion.

    Results retain plan order even if concurrent handlers finish out of order.
    Aggregate cancellation takes precedence over timeout, then adapter errors;
    every handler's original status and error remains available in its record.
    A normal return with unusable claims stays completed with unknown evidence.
    """
    if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 120):
        raise ValueError("deadline_seconds must be finite, positive and at most 120")
    plan = case["execution_plan"]
    mode = plan["mode"]
    tasks = plan["tasks"]
    if (mode not in {"single", "sequential", "concurrent"} or not isinstance(tasks, list)
            or not 1 <= len(tasks) <= 8 or (mode == "single" and len(tasks) != 1)
            or (mode == "concurrent" and not 2 <= len(tasks) <= 4)):
        raise ValueError("execution plan mode or invocation count is invalid")
    # Copy nested task data too: an adapter must not alter another delivery or
    # mutate the manifest subsequently used by the independent evaluator.
    envelopes = [TaskEnvelope(**copy.deepcopy(task)) for task in tasks]
    started = time.monotonic()
    wall_deadline = started + deadline_seconds
    simulator.context = None
    previous_barrier = simulator.refund_barrier
    barrier = threading.Barrier(len(envelopes)) if mode == "concurrent" else None
    simulator.refund_barrier = barrier
    if barrier is not None:
        simulator.record_event("concurrency_barrier_registered", {"handlers": len(envelopes)})

    def invoke(index: int, task: TaskEnvelope) -> dict:
        handler_id = f"handler_{index + 1}"
        invocation_started = time.monotonic()
        context = AgentContext(task.request_id, case["liveness"]["deadline_seconds"],
                               simulator.now, simulator.sleep, wall_deadline=wall_deadline,
                               cancel_signal=cancel_signal)
        simulator.bind_handler(handler_id, task.request_id)
        simulator.record_event("invocation_started", {"handler_id": handler_id,
                                                       "request_id": task.request_id})
        # Binding a context through simulator.tools() would overwrite the
        # context used by other threads. Budget checking belongs to this facade.
        tools = SupportTools(lambda tool, **args: (context.check_budget(),
                                                   simulator.dispatch(tool, **args))[1])
        execution = {"status": "completed", "error": None, "duration_ms": None}
        result = None
        try:
            context.check_budget()
            returned = agent(task, tools, context)
            returned_at = simulator.now()
            context.check_budget()
            result, execution["error"] = _structured_result(returned)
        except AgentCancelled as exc:
            returned_at = simulator.now()
            execution.update(status="cancelled", error=str(exc)[:2000])
        except BudgetExceeded as exc:
            returned_at = simulator.now()
            execution.update(status="timed_out", error=str(exc)[:2000])
        except Exception as exc:
            returned_at = simulator.now()
            execution.update(status="errored", error=f"{type(exc).__name__}: {str(exc)[:2000]}")
        execution["duration_ms"] = round((time.monotonic() - invocation_started) * 1000, 3)
        boundary = simulator.record_event("invocation_returned", {
            "handler_id": handler_id, "request_id": task.request_id,
            "returned_at": returned_at, "execution": execution,
        })
        record = {"request_id": task.request_id, "handler_id": handler_id, "result": result,
                  "execution": execution, "returned_at": returned_at,
                  "last_event_seq": int(boundary.rsplit("_", 1)[1])}
        simulator.save_invocation_result(handler_id, record)
        return record

    try:
        if mode == "concurrent":
            with ThreadPoolExecutor(max_workers=len(envelopes), thread_name_prefix="breakroom-handler") as executor:
                futures = [executor.submit(invoke, index, task) for index, task in enumerate(envelopes)]
                records = [future.result() for future in futures]
        else:
            records = [invoke(index, task) for index, task in enumerate(envelopes)]
    finally:
        simulator.refund_barrier = previous_barrier
        simulator.context = None
    statuses = {record["execution"]["status"] for record in records}
    status = next(value for value in ("cancelled", "timed_out", "errored", "completed") if value in statuses)
    errors = [f"{record['handler_id']}: {record['execution']['error']}"
              for record in records if record["execution"]["error"]]
    execution = {"status": status, "error": "; ".join(errors) if errors else None,
                 "duration_ms": round((time.monotonic() - started) * 1000, 3)}
    return records, execution
