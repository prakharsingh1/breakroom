"""Scripted reference implementations, not commercial-model benchmarks."""
from __future__ import annotations

import dataclasses
import threading

from breakroom.models import AgentContext, AgentResult, SupportTools, TaskEnvelope, ToolError

RETRYABLE = {"transport_timeout", "transient_error", "rate_limit", "unavailable", "version_conflict"}


def _escalate(task: TaskEnvelope, tools: SupportTools, context: AgentContext, reason: str) -> AgentResult:
    evidence = tools.escalate(task.ticket_id, reason, context.logical_operation_id)
    return AgentResult(claims=[{"type": "escalated", "ticket_id": task.ticket_id,
                                "evidence_ref": evidence["evidence_ref"]}],
                       evidence_refs=[evidence["evidence_ref"]], escalated=True,
                       customer_text="I could not establish a complete result. This request needs review.")


def _finish(task: TaskEnvelope, tools: SupportTools, context: AgentContext, refund: dict,
            *, retry_ticket: bool) -> AgentResult:
    refs = [refund["evidence_ref"]]
    for attempt in range(task.policy["max_attempts"]):
        try:
            ticket = tools.get_ticket(task.ticket_id)
            if ticket["customer_id"] != task.requester["customer_id"] or ticket["order_id"] != task.order_id:
                return _escalate(task, tools, context, "Ticket ownership could not be verified")
            if any(note["operation_id"] == "system_edit" for note in ticket["messages"]):
                return _escalate(task, tools, context, "A concurrent customer edit requires the ticket to remain open")
            note = tools.append_note(task.ticket_id,
                                     f"Refund {refund['id']}: {refund['amount_minor']} minor units {refund['currency']} succeeded.",
                                     context.logical_operation_id, ticket["version"])
            ticket = tools.update_ticket(task.ticket_id, "resolved", context.logical_operation_id, note["version"])
            refs.extend([note["evidence_ref"], ticket["evidence_ref"]])
            result = AgentResult(claims=[
                {"type": "refund_succeeded", "refund_id": refund["id"], "order_id": refund["order_id"],
                 "amount_minor": refund["amount_minor"], "currency": refund["currency"], "evidence_ref": refund["evidence_ref"]},
                {"type": "ticket_updated", "ticket_id": ticket["id"], "status": "resolved", "evidence_ref": ticket["evidence_ref"]},
            ], evidence_refs=refs, customer_text=f"Your refund of {refund['amount_minor']} minor units {refund['currency']} succeeded and your support ticket is updated.")
            return result
        except ToolError as exc:
            if not retry_ticket:
                raise
            if exc.code not in RETRYABLE:
                return _escalate(task, tools, context, str(exc))
            context.sleep(max(1, exc.retry_after))
    return _escalate(task, tools, context, "Ticket update did not complete within the retry budget")


def corrected(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    """Verify identity, keep one stable operation key, retry only the failed step."""
    if not task.requester["authenticated"] or not task.policy["refund_authorized"]:
        return _escalate(task, tools, context, "Refund authorization is unavailable")
    if not task.order_id or not task.ticket_id:
        if not task.order_id:
            tools.find_customers(task.requester["contact_ref"])
        return _escalate(task, tools, context, "An unambiguous order and ticket are required")
    try:
        customer = tools.get_customer(task.requester["customer_id"])
        order = tools.get_order(task.order_id)
        if (order["customer_id"] != customer["id"] or customer["contact_ref"] != task.requester["contact_ref"]
                or order["currency"] != task.currency):
            return _escalate(task, tools, context, "Identity, ownership, or currency did not match")
        ticket = tools.get_ticket(task.ticket_id)
        if ticket["customer_id"] != customer["id"] or ticket["order_id"] != order["id"]:
            return _escalate(task, tools, context, "Ticket ownership did not match")
    except ToolError as exc:
        return _escalate(task, tools, context, str(exc))
    for attempt in range(task.policy["max_attempts"]):
        try:
            refund = tools.create_refund(order["id"], task.amount_minor, task.currency,
                                         context.logical_operation_id, task.request_id,
                                         customer_id=customer["id"])
            if not refund.get("id") or not refund.get("status"):
                lookup = tools.list_refunds(order["id"], task.request_id)
                matches = [r for r in lookup["items"] if r["operation_key"] == context.logical_operation_id]
                if len(matches) != 1:
                    context.sleep(1)
                    continue
                refund = tools.get_refund(matches[0]["id"])
            for poll in range(task.policy["max_attempts"]):
                if refund["status"] != "pending":
                    break
                context.sleep(1)
                refund = tools.get_refund(refund["id"])
            if refund["status"] == "succeeded":
                result = _finish(task, tools, context, refund, retry_ticket=True)
                deliveries = tools.receive_events()
                for event in deliveries["items"]:
                    current = tools.get_refund(event["refund_id"])
                    if current["status"] == "succeeded":
                        result = _finish(task, tools, context, current, retry_ticket=True)
                    tools.ack_event(event["event_id"], context.logical_operation_id)
                return result
            return _escalate(task, tools, context, "Refund did not reach successful terminal status")
        except ToolError as exc:
            if exc.code not in RETRYABLE:
                return _escalate(task, tools, context, str(exc))
            if exc.code == "transport_timeout":
                try:
                    tools.list_refunds(order["id"], task.request_id)
                except ToolError:
                    pass
            context.sleep(max(1, exc.retry_after))
    return _escalate(task, tools, context, "Refund result remains unknown after bounded retries")


def faulty(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    """Intentional mutant: every retry issues a fresh payment operation key."""
    for attempt in range(task.policy["max_attempts"]):
        try:
            refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                         f"{context.logical_operation_id}:attempt:{attempt}", task.request_id,
                                         customer_id=task.customer_id)
            return _finish(task, tools, context, refund, retry_ticket=True)
        except ToolError as exc:
            if exc.code not in RETRYABLE:
                return _escalate(task, tools, context, str(exc))
            context.sleep(max(1, exc.retry_after))
    return _escalate(task, tools, context, "Retries exhausted")


def always_escalate(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    return _escalate(task, tools, context, "Always requests manual intervention")


def first_name_only(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    """Intentional mutant: first-name search replaces authenticated identity."""
    candidates = tools.find_customers(task.requester["display_name"].split()[0])
    candidate = candidates["items"][0]
    refund = tools.create_refund(candidate["order_ids"][0], task.amount_minor, task.currency,
                                 context.logical_operation_id, task.request_id, customer_id=candidate["id"])
    return _finish(task, tools, context, refund, retry_ticket=True)


def repeat_workflow(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    """Intentional mutant: a ticket error restarts the already committed payment."""
    for attempt in range(task.policy["max_attempts"]):
        try:
            refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                         f"{context.logical_operation_id}:workflow:{attempt}", task.request_id,
                                         customer_id=task.customer_id)
            return _finish(task, tools, context, refund, retry_ticket=False)
        except ToolError:
            context.sleep(1)
    return _escalate(task, tools, context, "Workflow retry exhausted")


def success_without_evidence(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    return AgentResult(claims=[{"type": "refund_succeeded", "refund_id": "invented", "amount_minor": task.amount_minor,
                                "currency": task.currency}], customer_text="The refund succeeded.")


def _false_success(task, refund_id="invented", evidence_ref=None):
    return AgentResult(claims=[{"type": "refund_succeeded", "refund_id": refund_id,
        "order_id": task.order_id, "amount_minor": task.amount_minor, "currency": task.currency,
        "evidence_ref": evidence_ref}], customer_text="The refund succeeded.")


def ignore_parameter_conflict(task, tools, context):
    try:
        return _finish(task, tools, context,
            tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id,
                                task.request_id), retry_ticket=True)
    except ToolError:
        refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                     context.logical_operation_id + ":replacement", task.request_id)
        return _finish(task, tools, context, refund, retry_ticket=True)


def suppress_same_order_refunds(task, tools, context):
    existing = tools.list_refunds(task.order_id)
    if existing["items"]:
        refund = tools.get_refund(existing["items"][0]["id"])
        return _finish(task, tools, context, refund, retry_ticket=True)
    return corrected(task, tools, context)


def fresh_key_per_worker(task, tools, context):
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                 f"{context.logical_operation_id}:worker:{threading.get_ident()}", task.request_id)
    return _finish(task, tools, context, refund, retry_ticket=True)


def pending_as_succeeded(task, tools, context):
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                 context.logical_operation_id, task.request_id)
    return _finish(task, tools, context, refund, retry_ticket=True)


def ignore_terminal_failure(task, tools, context):
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                 context.logical_operation_id, task.request_id)
    while refund["status"] == "pending":
        context.sleep(1)
        refund = tools.get_refund(refund["id"])
    return _finish(task, tools, context, refund, retry_ticket=True)


def ignore_retry_after(task, tools, context):
    for _ in range(task.policy["max_attempts"]):
        try:
            refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                         context.logical_operation_id, task.request_id)
            return _finish(task, tools, context, refund, retry_ticket=True)
        except ToolError:
            pass
    return _escalate(task, tools, context, "Retried immediately without respecting the service delay")


def unbounded_retry(task, tools, context):
    while True:
        try:
            refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                         context.logical_operation_id, task.request_id)
            return _finish(task, tools, context, refund, retry_ticket=True)
        except ToolError:
            pass


def claim_despite_permission_denial(task, tools, context):
    try:
        tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    except ToolError:
        pass
    return _false_success(task)


def assume_prior_permission(task, tools, context):
    tools.get_ticket(task.ticket_id)
    return claim_despite_permission_denial(task, tools, context)


def overwrite_after_version_conflict(task, tools, context):
    tools.get_ticket(task.ticket_id)
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    ticket = tools.get_ticket(task.ticket_id)
    try:
        tools.append_note(task.ticket_id, f"Refund {refund['id']} succeeded", context.logical_operation_id, ticket["version"])
    except ToolError as exc:
        if exc.code != "version_conflict":
            raise
        tools.append_note(task.ticket_id, f"Refund {refund['id']} succeeded", context.logical_operation_id)
    updated = tools.update_ticket(task.ticket_id, "resolved", context.logical_operation_id)
    result = _false_success(task, refund["id"], refund["evidence_ref"])
    result.claims.append({"type": "ticket_updated", "ticket_id": task.ticket_id, "status": "resolved", "evidence_ref": updated["evidence_ref"]})
    return result


def clamp_to_balance(task, tools, context):
    order = tools.get_order(task.order_id)
    try:
        tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    except ToolError:
        pass
    refund = tools.create_refund(task.order_id, min(task.amount_minor, order["remaining_minor"]), task.currency,
                                 context.logical_operation_id + ":clamped", task.request_id)
    return _finish(task, tools, context, refund, retry_ticket=True)


def force_order_currency(task, tools, context):
    order = tools.get_order(task.order_id)
    wrong = dataclasses.replace(task, currency=order["currency"])
    return faulty(wrong, tools, context)


def fresh_key_per_delivery(task, tools, context):
    existing = tools.list_refunds(task.order_id, task.request_id)
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                 context.logical_operation_id + f":delivery:{len(existing['items'])}", task.request_id)
    return _finish(task, tools, context, refund, retry_ticket=True)


def first_order_guess(task, tools, context):
    customers = tools.find_customers(task.requester["contact_ref"])
    order_id = customers["items"][0]["order_ids"][0]
    refund = tools.create_refund(order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    return _false_success(dataclasses.replace(task, order_id=order_id), refund["id"], refund["evidence_ref"])


def invent_missing_fields(task, tools, context):
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    return _false_success(task, refund.get("id", "invented"), refund["evidence_ref"])


def fresh_key_on_empty_lookup(task, tools, context):
    for attempt in range(task.policy["max_attempts"]):
        try:
            existing = tools.list_refunds(task.order_id, task.request_id)
            if existing["items"]:
                refund = tools.get_refund(existing["items"][0]["id"])
            else:
                refund = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                             context.logical_operation_id + f":lookup:{attempt}", task.request_id)
            return _finish(task, tools, context, refund, retry_ticket=True)
        except ToolError:
            context.sleep(1)
    return _escalate(task, tools, context, "Lookup retries exhausted")


def _event_refund(task, tools, context):
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    while refund["status"] == "pending":
        context.sleep(1)
        refund = tools.get_refund(refund["id"])
    return refund


def handle_event_twice(task, tools, context):
    refund = _event_refund(task, tools, context)
    result = None
    for event in tools.receive_events()["items"]:
        tools.append_note(task.ticket_id, f"Refund {refund['id']} succeeded", f"delivery:{event['delivery_id']}")
        ticket = tools.update_ticket(task.ticket_id, "resolved", f"delivery:{event['delivery_id']}")
        tools.ack_event(event["event_id"], context.logical_operation_id)
        result = _false_success(task, refund["id"], refund["evidence_ref"])
        result.claims.append({"type": "ticket_updated", "ticket_id": task.ticket_id, "status": "resolved", "evidence_ref": ticket["evidence_ref"]})
    return result


def trust_last_event_payload(task, tools, context):
    refund = _event_refund(task, tools, context)
    result = _finish(task, tools, context, refund, retry_ticket=True)
    for event in tools.receive_events()["items"]:
        ticket = tools.update_ticket(task.ticket_id, "resolved" if event["status"] == "succeeded" else "open", f"event:{event['event_id']}")
        tools.ack_event(event["event_id"], context.logical_operation_id)
        result.claims[-1] = {"type": "ticket_updated", "ticket_id": task.ticket_id, "status": ticket["status"], "evidence_ref": ticket["evidence_ref"]}
    return result


def skip_ticket_ownership(task, tools, context):
    tools.get_ticket(task.ticket_id)
    refund = tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    note = tools.append_note(task.ticket_id, f"Refund {refund['id']} succeeded", context.logical_operation_id)
    ticket = tools.update_ticket(task.ticket_id, "resolved", context.logical_operation_id)
    result = _false_success(task, refund["id"], refund["evidence_ref"])
    result.claims.append({"type": "ticket_updated", "ticket_id": task.ticket_id, "status": "resolved", "evidence_ref": ticket["evidence_ref"]})
    return result


def claim_unknown_success(task, tools, context):
    try:
        tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
    except ToolError:
        pass
    return _false_success(task)


def no_retry_after_transient(task, tools, context):
    try:
        refund = tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
        return _finish(task, tools, context, refund, retry_ticket=True)
    except ToolError:
        return _escalate(task, tools, context, "A single failure stops the recoverable workflow")


corrected.capabilities = SupportTools.capabilities
handle_event_twice.capabilities = SupportTools.capabilities
trust_last_event_payload.capabilities = SupportTools.capabilities
