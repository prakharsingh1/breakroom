"""Framework-neutral adapter types. Amounts are always integer minor units."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "0.2.0"
ORACLE_VERSION = "1.2"


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, retry_after: float = 0):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class BudgetExceeded(Exception):
    pass


class AgentCancelled(Exception):
    pass


@dataclass(frozen=True)
class TaskEnvelope:
    request_id: str
    requester: dict[str, Any]
    customer_id: str | None
    order_id: str | None
    ticket_id: str | None
    amount_minor: int
    currency: str
    text: str
    policy: dict[str, Any]


@dataclass
class AgentResult:
    claims: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    escalated: bool = False
    customer_text: str = ""


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


class AgentContext:
    """Controlled clock and budgets; no case, future faults, or oracle fields."""

    def __init__(self, logical_operation_id: str, deadline: float,
                 now: Callable[[], float], sleep: Callable[[float], None],
                 *, wall_deadline: float, cancel_signal: CancellationSignal | None = None):
        self.logical_operation_id = logical_operation_id
        self.deadline = deadline
        self._now = now
        self._sleep = sleep
        self._wall_deadline = wall_deadline
        self._cancel_signal = cancel_signal

    @property
    def cancelled(self) -> bool:
        return bool(self._cancel_signal and self._cancel_signal.is_set())

    def now(self) -> float:
        return self._now()

    def check_budget(self) -> None:
        if self.cancelled:
            raise AgentCancelled("Agent execution cancelled")
        if time.monotonic() >= self._wall_deadline or self.now() > self.deadline:
            raise BudgetExceeded("Agent execution budget exhausted")

    def sleep(self, seconds: float) -> None:
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sleep duration must be finite and non-negative")
        self.check_budget()
        if self.now() + seconds > self.deadline:
            raise BudgetExceeded("Requested sleep exceeds logical deadline")
        self._sleep(seconds)
        self.check_budget()


class SupportTools:
    """The only supported fake service operations given to local adapters.

    This facade prevents accidental leakage; trusted Python is not sandboxed.
    Every response carries an evidence_ref for structured outcome claims.
    """

    capabilities = frozenset({"payments", "tickets", "reconciliation", "virtual_clock", "events"})

    def __init__(self, dispatch: Callable[..., Any]):
        self._dispatch = dispatch

    def find_customers(self, query: str) -> dict:
        return self._dispatch("find_customers", query=query)

    def get_customer(self, customer_id: str) -> dict:
        return self._dispatch("get_customer", customer_id=customer_id)

    def get_order(self, order_id: str) -> dict:
        return self._dispatch("get_order", order_id=order_id)

    def list_refunds(self, order_id: str, logical_request_id: str | None = None) -> dict:
        return self._dispatch("list_refunds", order_id=order_id, logical_request_id=logical_request_id)

    def create_refund(self, order_id: str, amount_minor: int, currency: str,
                      operation_key: str, logical_request_id: str,
                      customer_id: str | None = None) -> dict:
        return self._dispatch("create_refund", order_id=order_id, amount_minor=amount_minor,
                              currency=currency, operation_key=operation_key,
                              logical_request_id=logical_request_id, customer_id=customer_id)

    def get_refund(self, refund_id: str) -> dict:
        return self._dispatch("get_refund", refund_id=refund_id)

    def get_ticket(self, ticket_id: str) -> dict:
        return self._dispatch("get_ticket", ticket_id=ticket_id)

    def append_note(self, ticket_id: str, text: str, operation_id: str,
                    expected_version: int | None = None) -> dict:
        return self._dispatch("append_note", ticket_id=ticket_id, text=text,
                              operation_id=operation_id, expected_version=expected_version)

    def update_ticket(self, ticket_id: str, status: str, operation_id: str,
                      expected_version: int | None = None) -> dict:
        return self._dispatch("update_ticket", ticket_id=ticket_id, status=status,
                              operation_id=operation_id, expected_version=expected_version)

    def escalate(self, ticket_id: str | None, reason: str, operation_id: str) -> dict:
        return self._dispatch("escalate", ticket_id=ticket_id, reason=reason,
                              operation_id=operation_id)

    def receive_events(self, limit: int = 8) -> dict:
        return self._dispatch("receive_events", limit=limit)

    def ack_event(self, event_id: str, consumer_operation_id: str) -> dict:
        return self._dispatch("ack_event", event_id=event_id, consumer_operation_id=consumer_operation_id)
