"""Complete offline adapter example. All application tool names map to fake services.

Run from this directory after installing the local core:
    breakroom run --agent adapter:run --pack support-refunds --case refund-response-lost --out artifacts/customer

This module is trusted local Python, not sandboxed or sent to a hosted runner.
"""
from breakroom import AgentContext, AgentResult, SupportTools, TaskEnvelope
from breakroom.models import ToolError

__version__ = "0.1.0"


class ApplicationTools:
    """Map your application's tool vocabulary onto the injected TestPay/TestDesk."""
    def __init__(self, tools: SupportTools, context: AgentContext, attempts: int):
        self.tools, self.context, self.attempts = tools, context, attempts

    def retry(self, operation, *args, **kwargs):
        for attempt in range(self.attempts):
            self.context.check_budget()
            try:
                return operation(*args, **kwargs)
            except ToolError as error:
                if error.code not in {"transient_error", "transport_timeout", "unavailable", "rate_limit"} or attempt + 1 == self.attempts:
                    raise
                self.context.sleep(max(1, error.retry_after))
        raise RuntimeError("Retry budget exhausted")

    def refund(self, task: TaskEnvelope):
        # One operation key survives retries, including a lost post-commit response.
        return self.retry(self.tools.create_refund, task.order_id, task.amount_minor,
                          task.currency, self.context.logical_operation_id,
                          task.request_id, task.customer_id)

    def finish_ticket(self, task: TaskEnvelope, refund: dict):
        note = self.retry(self.tools.append_note, task.ticket_id,
                          f'Refund {refund["id"]}: {task.amount_minor} minor units {task.currency}.',
                          self.context.logical_operation_id)
        ticket = self.retry(self.tools.update_ticket, task.ticket_id, "resolved",
                            self.context.logical_operation_id, expected_version=note["version"])
        return ticket


def run(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult:
    """Connect framework output here; this runnable example uses no model/network."""
    app = ApplicationTools(tools, context, task.policy["max_attempts"])
    evidence = []
    try:
        if not task.requester["authenticated"] or not task.policy["refund_authorized"]:
            raise ValueError("A verified authorized request is required")
        customer = tools.get_customer(task.requester["customer_id"])
        order = tools.get_order(task.order_id)
        ticket = tools.get_ticket(task.ticket_id)
        evidence += [customer["evidence_ref"], order["evidence_ref"], ticket["evidence_ref"]]
        if (customer["id"] != order["customer_id"] or customer["id"] != ticket["customer_id"]
                or ticket["order_id"] != order["id"] or order["currency"] != task.currency):
            raise ValueError("Identity, order, ticket, or currency does not match")
        refund = app.refund(task)
        evidence.append(refund["evidence_ref"])
        if not refund.get("id") or refund.get("status") != "succeeded":
            raise ValueError("No supported terminal refund evidence")
        ticket = app.finish_ticket(task, refund)
        evidence.append(ticket["evidence_ref"])
        return AgentResult(
            claims=[{"type": "refund_succeeded", "refund_id": refund["id"], "order_id": task.order_id,
                     "amount_minor": task.amount_minor, "currency": task.currency, "evidence_ref": refund["evidence_ref"]},
                    {"type": "ticket_updated", "ticket_id": task.ticket_id, "status": "resolved", "evidence_ref": ticket["evidence_ref"]}],
            evidence_refs=evidence,
            customer_text=f'Refund {refund["id"]} succeeded for {task.amount_minor} minor units {task.currency}; the ticket is updated.')
    except (ToolError, ValueError) as error:
        escalation = tools.escalate(task.ticket_id, str(error), context.logical_operation_id)
        return AgentResult(claims=[{"type": "escalated", "ticket_id": task.ticket_id, "evidence_ref": escalation["evidence_ref"]}],
                           evidence_refs=evidence + [escalation["evidence_ref"]], escalated=True,
                           customer_text="I could not verify completion and requested review.")
