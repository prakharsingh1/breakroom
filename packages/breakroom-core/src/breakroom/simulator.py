"""Transactional fake TestPay/TestDesk with durable effects and observed evidence.

Each tool operation owns a SQLite connection and explicit transaction. The unique
operation-key row is checked and inserted inside the SAME BEGIN IMMEDIATE write
transaction as the refund, including overlapping callers. No network is used.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import AgentContext, BudgetExceeded, SupportTools, ToolError


class Simulator:
    def __init__(self, case: dict, db_path: str | Path, *, initialize: bool = True):
        self.case = case
        self.db_path = str(db_path)
        self.context: AgentContext | None = None
        self._local = threading.local()
        self.behavior = case.get("behavior", {})
        # Test-only synchronization outside transactions: allows real overlap.
        self.refund_barrier: Any = None
        if initialize:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _initialize(self) -> None:
        with self.transaction() as con:
            # Existing files are not silently reset or shared across trials.
            con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("CREATE TABLE customers (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, contact_ref TEXT NOT NULL, tenant_id TEXT NOT NULL)")
            con.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), amount_minor INTEGER NOT NULL CHECK(amount_minor>0), currency TEXT NOT NULL, status TEXT NOT NULL)")
            con.execute("CREATE TABLE refunds (id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), amount_minor INTEGER NOT NULL CHECK(amount_minor>0), currency TEXT NOT NULL, logical_request_id TEXT NOT NULL, operation_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')), created_at REAL NOT NULL)")
            con.execute("CREATE TABLE idempotency (operation_key TEXT PRIMARY KEY, parameters TEXT NOT NULL, refund_id TEXT NOT NULL REFERENCES refunds(id))")
            con.execute("CREATE TABLE tickets (id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), order_id TEXT NOT NULL REFERENCES orders(id), version INTEGER NOT NULL, status TEXT NOT NULL)")
            con.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), text TEXT NOT NULL, operation_id TEXT NOT NULL, UNIQUE(ticket_id,operation_id))")
            con.execute("CREATE TABLE ticket_operations (operation_id TEXT NOT NULL, ticket_id TEXT NOT NULL REFERENCES tickets(id), parameters TEXT NOT NULL, PRIMARY KEY(ticket_id,operation_id))")
            con.execute("CREATE TABLE permissions (operation TEXT PRIMARY KEY, allowed INTEGER NOT NULL CHECK(allowed IN (0,1)))")
            con.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL, tool TEXT, data TEXT NOT NULL)")
            con.execute("CREATE TABLE invocations (tool TEXT PRIMARY KEY, count INTEGER NOT NULL)")
            con.execute("CREATE TABLE fault_counters (id TEXT PRIMARY KEY, triggered INTEGER NOT NULL)")
            con.execute("CREATE TABLE scheduled_events (id TEXT PRIMARY KEY, due_at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)")
            con.execute("CREATE TABLE invocation_results (handler_id TEXT PRIMARY KEY, record TEXT NOT NULL)")
            con.execute("CREATE TABLE event_deliveries (delivery_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, acked INTEGER NOT NULL DEFAULT 0)")
            con.execute("INSERT INTO meta VALUES ('clock','0')")
            con.execute("INSERT INTO meta VALUES ('initial_state',?)", (json.dumps(self.case["initial_state"]),))
            initial = self.case["initial_state"]
            for row in initial["customers"]:
                con.execute("INSERT INTO customers VALUES (?,?,?,?)", tuple(row[k] for k in ("id", "display_name", "contact_ref", "tenant_id")))
            for row in initial["orders"]:
                con.execute("INSERT INTO orders VALUES (?,?,?,?,?)", tuple(row[k] for k in ("id", "customer_id", "amount_minor", "currency", "status")))
            for row in initial["refunds"]:
                con.execute("INSERT INTO refunds VALUES (?,?,?,?,?,?,?,?)", tuple(row[k] for k in ("id", "order_id", "amount_minor", "currency", "logical_request_id", "operation_key", "status", "created_at")))
                parameters = self._parameters(row["order_id"], row["amount_minor"], row["currency"], row["logical_request_id"])
                con.execute("INSERT INTO idempotency VALUES (?,?,?)", (row["operation_key"], parameters, row["id"]))
            for row in initial["tickets"]:
                con.execute("INSERT INTO tickets VALUES (?,?,?,?,?)", tuple(row[k] for k in ("id", "customer_id", "order_id", "version", "status")))
            for row in initial["permissions"]:
                con.execute("INSERT INTO permissions VALUES (?,?)", (row["operation"], int(row["allowed"])))
            for fault in self.case["faults"]:
                con.execute("INSERT INTO fault_counters VALUES (?,0)", (fault["id"],))

    @staticmethod
    def _parameters(order_id: str, amount_minor: int, currency: str, logical_request_id: str) -> str:
        return json.dumps([order_id, amount_minor, currency, logical_request_id], separators=(",", ":"))

    @staticmethod
    def _clock(con: sqlite3.Connection) -> float:
        return float(con.execute("SELECT value FROM meta WHERE key='clock'").fetchone()[0])

    def now(self) -> float:
        con = self._connect()
        try:
            return self._clock(con)
        finally:
            con.close()

    def _event(self, con: sqlite3.Connection, kind: str, tool: str | None, data: dict) -> str:
        if getattr(self._local, "handler_id", None):
            data = {**data, "handler_id": self._local.handler_id, "request_id": self._local.request_id}
        cursor = con.execute("INSERT INTO events(at,kind,tool,data) VALUES (?,?,?,?)",
                             (self._clock(con), kind, tool, json.dumps(data, sort_keys=True)))
        return f"evt_{cursor.lastrowid:06d}"

    def bind_handler(self, handler_id: str, request_id: str) -> None:
        self._local.handler_id = handler_id
        self._local.request_id = request_id
        self._local.first_refund = True

    def record_event(self, kind: str, data: dict) -> str:
        with self.transaction() as con:
            return self._event(con, kind, None, data)

    def save_invocation_result(self, handler_id: str, record: dict) -> None:
        with self.transaction() as con:
            con.execute("INSERT OR REPLACE INTO invocation_results VALUES (?,?)", (handler_id, json.dumps(record)))

    def recovered_invocation_results(self) -> list[dict]:
        with self.transaction() as con:
            return [json.loads(row[0]) for row in con.execute("SELECT record FROM invocation_results ORDER BY handler_id")]

    def sleep(self, seconds: float) -> None:
        with self.transaction() as con:
            now = self._clock(con) + seconds
            con.execute("UPDATE meta SET value=? WHERE key='clock'", (str(now),))
            self._event(con, "clock_advanced", None, {"seconds": seconds})
            pending = con.execute("SELECT * FROM scheduled_events WHERE due_at<=? AND delivered=0 ORDER BY due_at,id", (now,)).fetchall()
            for row in pending:
                payload = json.loads(row["data"])
                if row["kind"] == "refund_transition":
                    con.execute("UPDATE refunds SET status=? WHERE id=? AND status='pending'", (payload["status"], payload["refund_id"]))
                    refund = dict(con.execute("SELECT * FROM refunds WHERE id=?", (payload["refund_id"],)).fetchone())
                    self._event(con, "refund_transition", None, {"refund": refund, "status": payload["status"]})
                    if self.behavior.get("emit_events"):
                        self._queue_notifications(con, refund)
                con.execute("UPDATE scheduled_events SET delivered=1 WHERE id=?", (row["id"],))
                self._event(con, "event_delivered", None, {"event_id": row["id"], "event_kind": row["kind"], **payload})

    def _queue_notifications(self, con: sqlite3.Connection, refund: dict) -> None:
        terminal = {"event_id": f"event_{refund['id']}_{refund['status']}", "refund_id": refund["id"],
                    "logical_request_id": refund["logical_request_id"], "status": refund["status"],
                    "created_at": self._clock(con), "order_id": refund["order_id"]}
        deliveries = [terminal]
        if self.behavior.get("event_delivery") == "duplicate":
            deliveries.append(dict(terminal))
        elif self.behavior.get("event_delivery") == "reordered":
            deliveries.append({**terminal, "event_id": f"event_{refund['id']}_pending", "status": "pending", "created_at": refund["created_at"]})
        for event in deliveries:
            con.execute("INSERT INTO event_deliveries(event_id,payload) VALUES (?,?)", (event["event_id"], json.dumps(event)))
            self._event(con, "event_queued", None, {"event": event})

    def schedule_refund_transition(self, refund_id: str, status: str, delay: int) -> None:
        """Maintainer/test setup only, never part of the agent tool facade."""
        if status not in {"succeeded", "failed"} or type(delay) is not int or delay < 0:
            raise ValueError("invalid terminal transition")
        with self.transaction() as con:
            con.execute("UPDATE refunds SET status='pending' WHERE id=?", (refund_id,))
            con.execute("INSERT INTO scheduled_events(id,due_at,kind,data) VALUES (?,?,?,?)", (f"transition_{refund_id}", self._clock(con) + delay, "refund_transition", json.dumps({"refund_id": refund_id, "status": status})))

    def tools(self, context: AgentContext | None = None) -> SupportTools:
        self.context = context
        return SupportTools(self.dispatch)

    def _fault(self, tool: str, invocation: int, phase: str) -> dict | None:
        fault = next((f for f in self.case["faults"] if f["tool"] == tool and f["invocation"] == invocation and f["phase"] == phase), None)
        if fault is None:
            return None
        with self.transaction() as con:
            con.execute("UPDATE fault_counters SET triggered=triggered+1 WHERE id=?", (fault["id"],))
            self._event(con, "fault_triggered", tool, {"fault_id": fault["id"], "phase": phase, "action": fault["action"], "invocation": invocation})
        return fault

    def dispatch(self, tool: str, **arguments: Any) -> dict:
        if self.context:
            self.context.check_budget()
        with self.transaction() as con:
            total = con.execute("SELECT COALESCE(SUM(count),0) FROM invocations").fetchone()[0]
            if total >= self.case["liveness"]["max_tool_calls"]:
                raise BudgetExceeded("Maximum tool-call budget exhausted")
            con.execute("INSERT INTO invocations VALUES (?,1) ON CONFLICT(tool) DO UPDATE SET count=count+1", (tool,))
            invocation = con.execute("SELECT count FROM invocations WHERE tool=?", (tool,)).fetchone()[0]
            self._event(con, "tool_call", tool, {"invocation": invocation, "arguments": arguments})
        try:
            fault = self._fault(tool, invocation, "before")
            if fault:
                raise ToolError(fault["action"], "Test service refused the operation before any write", retry_after=fault.get("retry_after", 0))
            if tool == "create_refund" and self.refund_barrier is not None and getattr(self._local, "first_refund", True):
                self._local.first_refund = False
                self.record_event("barrier_arrived", {"tool": tool})
                self.refund_barrier.wait(timeout=5)
            if tool == "create_refund" and self.now() < self.behavior.get("rate_limit_until", 0):
                self.record_event("rate_limited", {"retry_at": self.behavior["rate_limit_until"]})
                raise ToolError("rate_limit", "Rate limit is active until the given virtual time", retry_after=self.behavior["rate_limit_until"] - self.now())
            with self.transaction() as con:
                response = self._operation(con, tool, arguments)
            fault = self._fault(tool, invocation, "after_commit")
            if fault:
                raise ToolError("transport_timeout", "The response was lost; the operation outcome is unknown")
            if tool in {"create_refund", "list_refunds", "get_refund"} and self.now() < self.behavior.get("hidden_until", 0):
                if tool == "list_refunds" and self.now() < self.behavior.get("stale_until", 0):
                    self.record_event("stale_read", {"tool": tool, "visible_after": self.behavior["stale_until"]})
                    response = {"items": []}
                else:
                    self.record_event("hidden_response", {"tool": tool})
                    raise ToolError("transport_timeout", "The service cannot currently establish a result")
            fault = self._fault(tool, invocation, "read_response")
            if fault:
                response = {"items": []} if fault["action"] == "stale" else {k: v for k, v in response.items() if k not in {"id", "status"}}
            with self.transaction() as con:
                ref = self._event(con, "tool_response", tool, {"invocation": invocation, "response": response})
            return {**response, "evidence_ref": ref}
        except ToolError as error:
            with self.transaction() as con:
                self._event(con, "tool_error", tool, {"invocation": invocation, "code": error.code, "message": str(error), "retry_after": error.retry_after})
            raise

    def _visible_customer(self, con: sqlite3.Connection, customer_id: str) -> dict:
        row = con.execute("SELECT * FROM customers WHERE id=? AND tenant_id=?", (customer_id, self.case["task"]["requester"]["tenant_id"])).fetchone()
        if row is None:
            raise ToolError("not_found", "Customer not found in the current account")
        return dict(row)

    def _order(self, con: sqlite3.Connection, order_id: str) -> dict:
        row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise ToolError("not_found", "Order not found")
        self._visible_customer(con, row["customer_id"])
        return dict(row)

    def _ticket(self, con: sqlite3.Connection, ticket_id: str) -> dict:
        row = con.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if row is None:
            raise ToolError("not_found", "Ticket not found")
        self._visible_customer(con, row["customer_id"])
        return {**dict(row), "messages": [dict(note) for note in con.execute("SELECT * FROM notes WHERE ticket_id=? ORDER BY id", (ticket_id,))]}

    @staticmethod
    def _permission(con: sqlite3.Connection, operation: str) -> None:
        row = con.execute("SELECT allowed FROM permissions WHERE operation=?", (operation,)).fetchone()
        if row is None or not row[0]:
            raise ToolError("permission_denied", "Required synthetic permission is unavailable")

    def _operation(self, con: sqlite3.Connection, tool: str, args: dict) -> dict:
        if tool == "find_customers":
            query = args["query"].casefold().strip()
            customers = [dict(row) for row in con.execute("SELECT * FROM customers WHERE tenant_id=? ORDER BY id", (self.case["task"]["requester"]["tenant_id"],)) if query in row["display_name"].casefold() or query in row["contact_ref"].casefold()]
            for customer in customers:
                customer["order_ids"] = [row[0] for row in con.execute("SELECT id FROM orders WHERE customer_id=? ORDER BY id", (customer["id"],))]
            return {"items": customers}
        if tool == "get_customer":
            return self._visible_customer(con, args["customer_id"])
        if tool == "get_order":
            order = self._order(con, args["order_id"])
            refunded = con.execute("SELECT COALESCE(SUM(amount_minor),0) FROM refunds WHERE order_id=? AND status IN ('pending','succeeded')", (order["id"],)).fetchone()[0]
            if order["currency"] != self.case["task"]["currency"]:
                self._event(con, "currency_observed", tool, {"currency": order["currency"]})
            return {**order, "remaining_minor": order["amount_minor"] - refunded}
        if tool == "list_refunds":
            self._order(con, args["order_id"])
            rows = con.execute("SELECT * FROM refunds WHERE order_id=? AND (? IS NULL OR logical_request_id=?) ORDER BY id", (args["order_id"], args["logical_request_id"], args["logical_request_id"]))
            return {"items": [dict(row) for row in rows]}
        if tool == "get_refund":
            row = con.execute("SELECT * FROM refunds WHERE id=?", (args["refund_id"],)).fetchone()
            if row is None:
                raise ToolError("not_found", "Refund not found")
            self._order(con, row["order_id"])
            return dict(row)
        if tool == "create_refund":
            return self._refund(con, args)
        if tool == "get_ticket":
            ticket = self._ticket(con, args["ticket_id"])
            if ticket["customer_id"] != self.case["task"]["requester"]["customer_id"] or ticket["order_id"] != self.case["task"]["order_id"]:
                self._event(con, "ticket_mismatch_observed", tool, {"ticket_id": ticket["id"]})
            if self.behavior.get("revoke_after_ticket_read") and not con.execute("SELECT 1 FROM meta WHERE key='revoked'").fetchone():
                con.execute("UPDATE permissions SET allowed=0 WHERE operation='refund'")
                con.execute("INSERT INTO meta VALUES ('revoked','1')")
                self._event(con, "permission_revoked", tool, {"operation": "refund", "actor": "system"})
            ticket_read_count = con.execute("SELECT count FROM invocations WHERE tool='get_ticket'").fetchone()[0]
            if self.behavior.get("edit_after_ticket_read") and ticket_read_count == 2 and not con.execute("SELECT 1 FROM meta WHERE key='ticket_edited'").fetchone():
                con.execute("INSERT INTO notes VALUES (?,?,?,?)", ("system_note_1", ticket["id"], "Customer requested the ticket remain open for a delivery-address review.", "system_edit"))
                con.execute("UPDATE tickets SET version=version+1,status='open' WHERE id=?", (ticket["id"],))
                con.execute("INSERT INTO meta VALUES ('ticket_edited','1')")
                self._event(con, "system_ticket_edit", tool, {"ticket": self._ticket(con, ticket["id"]), "actor": "system"})
            return ticket
        if tool in {"append_note", "update_ticket"}:
            return self._ticket_write(con, tool, args)
        if tool == "escalate":
            if args["ticket_id"] is not None:
                self._ticket(con, args["ticket_id"])
            self._event(con, "escalation_requested", tool, args)
            return {"status": "escalated", "ticket_id": args["ticket_id"], "operation_id": args["operation_id"]}
        if tool == "receive_events":
            if type(args["limit"]) is not int or not 1 <= args["limit"] <= 16:
                raise ToolError("invalid_request", "Event receive limit must be 1..16")
            rows = con.execute("SELECT * FROM event_deliveries WHERE delivered=0 ORDER BY delivery_id LIMIT ?", (args["limit"],)).fetchall()
            items = []
            for row in rows:
                payload = {**json.loads(row["payload"]), "delivery_id": row["delivery_id"]}
                con.execute("UPDATE event_deliveries SET delivered=1 WHERE delivery_id=?", (row["delivery_id"],))
                self._event(con, "event_received", tool, {"event": payload})
                items.append(payload)
            return {"items": items}
        if tool == "ack_event":
            rows = con.execute("SELECT * FROM event_deliveries WHERE event_id=? AND delivered=1", (args["event_id"],)).fetchall()
            if not rows:
                raise ToolError("not_found", "Event was not received")
            con.execute("UPDATE event_deliveries SET acked=1 WHERE event_id=? AND delivered=1", (args["event_id"],))
            self._event(con, "event_acknowledged", tool, args)
            return {"event_id": args["event_id"], "status": "acknowledged"}
        raise ToolError("unsupported_tool", "Unsupported fake tool")

    def _refund(self, con: sqlite3.Connection, args: dict) -> dict:
        self._permission(con, "refund")
        amount = args["amount_minor"]
        if type(amount) is not int or not 0 < amount <= 10**12:
            raise ToolError("invalid_amount", "Refund amount must be a positive integer in minor units")
        for key in ("operation_key", "logical_request_id"):
            if not isinstance(args[key], str) or not 0 < len(args[key]) <= 200:
                raise ToolError("invalid_request", f"Invalid {key}")
        order = self._order(con, args["order_id"])
        if args.get("customer_id") is not None and args["customer_id"] != order["customer_id"]:
            raise ToolError("ownership_mismatch", "Order does not belong to the supplied customer")
        if args["currency"] != order["currency"]:
            raise ToolError("currency_mismatch", "Refund currency differs from the original payment")
        parameters = self._parameters(order["id"], amount, args["currency"], args["logical_request_id"])
        registry = con.execute("SELECT * FROM idempotency WHERE operation_key=?", (args["operation_key"],)).fetchone()
        if registry:
            if registry["parameters"] != parameters:
                raise ToolError("idempotency_conflict", "Operation key was already used with different parameters")
            self._event(con, "idempotency_reused", "create_refund", {"operation_key": args["operation_key"], "refund_id": registry["refund_id"]})
            return dict(con.execute("SELECT * FROM refunds WHERE id=?", (registry["refund_id"],)).fetchone())
        refunded = con.execute("SELECT COALESCE(SUM(amount_minor),0) FROM refunds WHERE order_id=? AND status IN ('pending','succeeded')", (order["id"],)).fetchone()[0]
        if order["status"] != "paid" or amount > order["amount_minor"] - refunded:
            raise ToolError("insufficient_refundable_balance", "Refund exceeds the remaining paid balance")
        index = con.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] + 1
        refund_id = f"rf_{index:06d}"
        while con.execute("SELECT 1 FROM refunds WHERE id=?", (refund_id,)).fetchone():
            index += 1
            refund_id = f"rf_{index:06d}"
        status = self.behavior.get("refund_initial_status", "succeeded")
        con.execute("INSERT INTO refunds VALUES (?,?,?,?,?,?,?,?)", (refund_id, order["id"], amount, args["currency"], args["logical_request_id"], args["operation_key"], status, self._clock(con)))
        con.execute("INSERT INTO idempotency VALUES (?,?,?)", (args["operation_key"], parameters, refund_id))
        refund = dict(con.execute("SELECT * FROM refunds WHERE id=?", (refund_id,)).fetchone())
        self._event(con, "refund_committed", "create_refund", {"refund": refund, "permission": "refund", "permission_allowed": True})
        if status == "pending":
            due = self._clock(con) + self.behavior["transition_delay"]
            con.execute("INSERT INTO scheduled_events(id,due_at,kind,data) VALUES (?,?,?,?)", (f"transition_{refund_id}", due, "refund_transition", json.dumps({"refund_id": refund_id, "status": self.behavior["terminal_status"]})))
        elif self.behavior.get("emit_events"):
            self._queue_notifications(con, refund)
        return refund

    def _ticket_write(self, con: sqlite3.Connection, tool: str, args: dict) -> dict:
        self._permission(con, "ticket_write")
        ticket = self._ticket(con, args["ticket_id"])
        if not isinstance(args["operation_id"], str) or not 0 < len(args["operation_id"]) <= 200:
            raise ToolError("invalid_request", "Invalid operation identifier")
        parameter = json.dumps([tool, args.get("text"), args.get("status")])
        op_key = f"{tool}:{args['operation_id']}"
        previous = con.execute("SELECT parameters FROM ticket_operations WHERE ticket_id=? AND operation_id=?", (ticket["id"], op_key)).fetchone()
        if previous:
            if previous[0] != parameter:
                raise ToolError("idempotency_conflict", "Ticket operation parameters changed")
            return self._ticket(con, ticket["id"])
        if args.get("expected_version") is not None and args["expected_version"] != ticket["version"]:
            raise ToolError("version_conflict", "Ticket has changed; read the latest version")
        if tool == "append_note":
            if not isinstance(args["text"], str) or len(args["text"]) > 4000:
                raise ToolError("invalid_request", "Note exceeds bounds")
            index = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] + 1
            con.execute("INSERT INTO notes VALUES (?,?,?,?)", (f"note_{index:06d}", ticket["id"], args["text"], args["operation_id"]))
            con.execute("UPDATE tickets SET version=version+1 WHERE id=?", (ticket["id"],))
        else:
            if args["status"] not in {"open", "resolved", "escalated"}:
                raise ToolError("invalid_request", "Invalid ticket status")
            con.execute("UPDATE tickets SET status=?,version=version+1 WHERE id=?", (args["status"], ticket["id"]))
        con.execute("INSERT INTO ticket_operations VALUES (?,?,?)", (op_key, ticket["id"], parameter))
        self._event(con, "ticket_committed", tool, {"ticket_id": ticket["id"], "customer_id": ticket["customer_id"], "order_id": ticket["order_id"], "operation_id": args["operation_id"], "permission_allowed": True, "ticket": self._ticket(con, ticket["id"])})
        return self._ticket(con, ticket["id"])

    def snapshot(self) -> dict:
        with self.transaction() as con:
            state = {name: [dict(row) for row in con.execute(f"SELECT * FROM {name} ORDER BY 1")] for name in ("customers", "orders", "refunds", "tickets", "permissions", "idempotency", "fault_counters", "scheduled_events", "event_deliveries")}
            for ticket in state["tickets"]:
                ticket["messages"] = [dict(row) for row in con.execute("SELECT * FROM notes WHERE ticket_id=? ORDER BY id", (ticket["id"],))]
            state["clock"] = self._clock(con)
            state["invocations"] = {row["tool"]: row["count"] for row in con.execute("SELECT * FROM invocations")}
            return state

    def events(self) -> list[dict]:
        with self.transaction() as con:
            return [{"id": f"evt_{row['seq']:06d}", "seq": row["seq"], "at": row["at"], "kind": row["kind"], "tool": row["tool"], "data": json.loads(row["data"])} for row in con.execute("SELECT * FROM events ORDER BY seq")]

    def save_result(self, result: dict) -> None:
        with self.transaction() as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('agent_result',?)", (json.dumps(result),))

    def recovered_result(self) -> dict | None:
        with self.transaction() as con:
            row = con.execute("SELECT value FROM meta WHERE key='agent_result'").fetchone()
            return json.loads(row[0]) if row else None
