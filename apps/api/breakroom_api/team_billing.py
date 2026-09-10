"""Optional test-mode billing: durable operations, signed inbox, current-state reconciliation.

Provider event timestamps are evidence, never an ordering mechanism. A worker
serializes reconciliation per project and retrieves current provider objects.
Only the resulting validated paid period can grant new report-writing access.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Text, and_, cast, func, insert, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB

from .billing_db import accounts, events, operations
from .billing_provider import BillingConfig, BillingInvalidRequest, BillingUnavailable, StripeProvider
from .team_db import memberships, opaque_id, projects, reports, utcnow

TERMINAL = {"canceled", "incomplete_expired"}
STATUSES = {"active", "trialing", "past_due", "canceled", "unpaid", "incomplete", "incomplete_expired", "paused"}
EVENT_TYPES = {"checkout.session.completed", "checkout.session.expired", "customer.subscription.created",
    "customer.subscription.updated", "customer.subscription.deleted", "invoice.paid",
    "invoice.payment_failed", "invoice.finalization_failed"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def identifier(value, prefix):
    return isinstance(value, str) and bool(re.fullmatch(prefix + r"_[A-Za-z0-9_]{1,120}", value))


def stamp(value):
    if type(value) is not int or not 0 < value < 32503680000:
        raise BillingInvalidRequest()
    return datetime.fromtimestamp(value, timezone.utc)


class CheckoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan: str


class CancelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    at_period_end: bool = True


class BillingService:
    def __init__(self, settings, store, provider=None):
        self.settings, self.store = settings, store
        self.config = BillingConfig(mode=settings.billing_mode, secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret, price_id=settings.stripe_price_id,
            public_origin=settings.public_origin, unit_amount=settings.billing_amount_minor,
            currency=settings.billing_currency)
        self.config.validate()
        self.provider = provider or StripeProvider(self.config)

    @property
    def enabled(self):
        return self.settings.billing_mode == "test"

    def require_enabled(self):
        if not self.enabled:
            raise HTTPException(503, "Billing is disabled. No test checkout or live payment is available.")

    def account(self, con, project_id, *, create=False):
        row = con.execute(select(accounts).where(accounts.c.project_id == project_id)).mappings().first()
        if row:
            return dict(row)
        value = {"project_id": project_id, "customer_id": None, "subscription_id": None,
            "status": "none", "sync_state": "ready", "cancel_at_period_end": False,
            "current_period_end": None, "paid_through": None, "updated_at": utcnow()}
        if create:
            con.execute(insert(accounts).values(**value))
        return value

    def usage(self, con, project_id):
        seats = con.execute(select(func.count()).select_from(memberships).where(memberships.c.project_id == project_id)).scalar_one()
        count, storage = con.execute(select(func.count(), func.coalesce(func.sum(func.octet_length(cast(reports.c.upload, Text))), 0)).where(reports.c.project_id == project_id)).one()
        return {"seats": seats, "reports": count, "storage_bytes": int(storage)}

    def view(self, con, project_id):
        account = self.account(con, project_id)
        paid = bool(account["status"] == "active" and account["sync_state"] in {"ready", "retrying"}
            and account["paid_through"] and account["paid_through"] > utcnow())
        can_cancel = bool(self.enabled and account["subscription_id"] and account["status"] not in TERMINAL)
        limits = {"seats": self.settings.billing_seat_limit, "reports": self.settings.billing_report_limit,
            "storage_bytes": self.settings.billing_storage_limit} if self.enabled else {
            "seats": 25, "reports": 1000, "storage_bytes": 256 * 1024 * 1024}
        return {"mode": self.settings.billing_mode, "configured": self.enabled, "provider": "stripe",
            "amount_minor": self.config.unit_amount, "currency": self.config.currency.upper(), "interval": "month",
            "status": account["status"] if self.enabled else "disabled",
            "access": "local_development" if not self.enabled else "paid" if paid else "read_only",
            "sync_state": account["sync_state"], "cancel_at_period_end": account["cancel_at_period_end"],
            "current_period_end": account["current_period_end"], "paid_through": account["paid_through"],
            "can_cancel": can_cancel, "checkout_available": bool(self.enabled and not can_cancel),
            "limits": limits, "usage": self.usage(con, project_id),
            "notice": "Stripe test mode only. No live payment, merchant approval or production billing readiness is claimed."
                if self.enabled else "Billing is disabled; the team workflow uses explicit local development limits."}

    def enforce(self, con, project_id, *, added_seats=0, upload=None):
        view = self.view(con, project_id)
        if view["access"] == "read_only":
            raise HTTPException(402, "A verified paid test subscription is required for new report imports or added seats. Retained reports remain readable.")
        additional_bytes = 0 if upload is None else con.execute(select(func.octet_length(cast(literal(upload, type_=JSONB), Text)))).scalar_one()
        additions = {"seats": added_seats, "reports": int(upload is not None), "storage_bytes": additional_bytes}
        for key, addition in additions.items():
            if addition and view["usage"][key] + addition > view["limits"][key]:
                raise HTTPException(409, f"Project {key.replace('_', ' ')} limit reached. Remove retained data or review the configured plan limits.")

    def operation(self, con, project_id, kind, key, request):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise HTTPException(422, "Idempotency-Key must be a stable 64-character lowercase hexadecimal digest")
        row = con.execute(select(operations).where(operations.c.project_id == project_id,
            operations.c.kind == kind, operations.c.idempotency_key == key)).mappings().first()
        if row:
            if row["request_hash"] != digest(request):
                raise HTTPException(409, "Idempotency key was already used for different billing parameters")
            if row["status"] != "completed" and row["created_at"] < utcnow() - timedelta(hours=23):
                raise HTTPException(409, "Uncertain billing operation requires provider reconciliation; its retry horizon expired")
            return dict(row)
        if kind == "checkout":
            old = con.execute(select(operations).where(operations.c.project_id == project_id, operations.c.kind == kind).order_by(operations.c.created_at.desc()).limit(1)).mappings().all()
            account = self.account(con, project_id)
            for item in old:
                terminal = account["subscription_id"] and account["status"] in TERMINAL
                if item["status"] != "completed" or not item["result"] or (item["result"].get("expires_at", 0) > utcnow().timestamp() and not terminal and not item["result"].get("confirmed_expired")):
                    raise HTTPException(409, "A checkout already exists or is uncertain; retry its original request or reconcile it before starting another")
                try:
                    previous = self.provider.retrieve_checkout(item["result"]["checkout_id"])
                except (BillingUnavailable, BillingInvalidRequest):
                    raise HTTPException(503, "Previous checkout state is unavailable; a replacement was not created") from None
                completed_terminal = terminal and previous.get("subscription") == account["subscription_id"]
                if ((not completed_terminal and (previous.get("status") != "expired" or previous.get("subscription") is not None))
                        or previous.get("customer") != account["customer_id"] or previous.get("livemode") is not False):
                    raise HTTPException(409, "Previous checkout has not been confirmed expired; reconcile its subscription before starting another")
        now = utcnow()
        row = {"id": opaque_id(), "project_id": project_id, "kind": kind, "idempotency_key": key,
            "request_hash": digest(request), "status": "pending", "result": None, "created_at": now, "updated_at": now}
        con.execute(insert(operations).values(**row))
        return row

    def checkout(self, project_id, key, guard):
        # The reservation is committed separately from provider calls so a lost
        # response cannot be bypassed by issuing a fresh request key.
        with self.store.engine.begin() as con:
            guard(con)
            account = self.account(con, project_id, create=True)
            if account["subscription_id"] and account["status"] not in TERMINAL:
                raise HTTPException(409, "This project already has a subscription; manage it before creating another")
            operation = self.operation(con, project_id, "checkout", key, {"plan": "team"})
            if operation["status"] == "completed":
                if operation["result"]["expires_at"] <= utcnow().timestamp():
                    raise HTTPException(410, "The checkout expired; start a new request")
                return {**operation["result"], "reused": True}
        error = False
        with self.store.engine.begin() as con:
            guard(con)
            current = con.execute(select(operations).where(operations.c.id == operation["id"])).mappings().one()
            if current["status"] == "completed":
                return {**current["result"], "reused": True}
            account = self.account(con, project_id)
            if account["subscription_id"] and account["status"] not in TERMINAL:
                raise HTTPException(409, "A subscription was confirmed while checkout was pending; another checkout was not created")
            try:
                if not account["customer_id"]:
                    customer = self.provider.create_customer(project_id, "customer:" + operation["id"])
                    if not identifier(customer.get("id"), "cus") or customer.get("livemode") is not False:
                        raise BillingInvalidRequest()
                    account["customer_id"] = customer["id"]
                    con.execute(update(accounts).where(accounts.c.project_id == project_id).values(customer_id=customer["id"]))
                url = self.settings.public_origin + "/projects/" + project_id
                session = self.provider.create_checkout(account["customer_id"], project_id, url + "?billing=returned", url + "?billing=canceled", "checkout:" + operation["id"])
                if not identifier(session.get("id"), "cs") or session.get("livemode") is not False or session.get("mode") != "subscription":
                    raise BillingInvalidRequest()
                if session.get("customer") != account["customer_id"] or not isinstance(session.get("url"), str) or not session["url"].startswith("https://checkout.stripe.com/"):
                    raise BillingInvalidRequest()
                expires = stamp(session.get("expires_at"))
                result = {"checkout_id": session["id"], "url": session["url"], "expires_at": int(expires.timestamp()), "test_mode": True}
                con.execute(update(operations).where(operations.c.id == operation["id"]).values(status="completed", result=result, updated_at=utcnow()))
                con.execute(update(accounts).where(accounts.c.project_id == project_id).values(status="pending", sync_state="pending",
                    subscription_id=None, paid_through=None, current_period_end=None, cancel_at_period_end=False, updated_at=utcnow()))
            except (BillingUnavailable, BillingInvalidRequest):
                con.execute(update(operations).where(operations.c.id == operation["id"]).values(status="unknown", updated_at=utcnow()))
                con.execute(update(accounts).where(accounts.c.project_id == project_id).values(sync_state="retrying", updated_at=utcnow()))
                error = True
        if error:
            raise HTTPException(503, "Checkout result is uncertain; retry this exact Idempotency-Key. No access was granted.")
        return {**result, "reused": False}

    def apply_snapshot(self, con, project_id, snapshot):
        try:
            return self._apply_snapshot(con, project_id, snapshot)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
            raise BillingInvalidRequest() from None

    def _apply_snapshot(self, con, project_id, snapshot):
        account = self.account(con, project_id)
        if (not isinstance(snapshot, dict) or not identifier(snapshot.get("id"), "sub")
                or snapshot.get("customer") != account["customer_id"] or snapshot.get("livemode") is not False
                or snapshot.get("status") not in STATUSES):
            raise BillingInvalidRequest()
        if account["subscription_id"] and account["subscription_id"] != snapshot["id"]:
            # A callback from an old subscription must never overwrite a new one.
            return False
        if not account["subscription_id"]:
            latest = con.execute(select(operations).where(operations.c.project_id == project_id,
                operations.c.kind == "checkout", operations.c.status == "completed").order_by(operations.c.created_at.desc()).limit(1)).mappings().first()
            if not latest or not latest["result"]:
                raise BillingUnavailable()
            checkout = self.provider.retrieve_checkout(latest["result"]["checkout_id"])
            selected = checkout.get("subscription")
            if isinstance(selected, dict):
                selected = selected.get("id")
            if selected != snapshot["id"]:
                return False
            if checkout.get("customer") != account["customer_id"] or checkout.get("livemode") is not False:
                raise BillingInvalidRequest()
        items = snapshot.get("items", {}).get("data")
        if not isinstance(items, list) or len(items) != 1 or snapshot.get("items", {}).get("has_more") is True:
            raise BillingInvalidRequest()
        item, price = items[0], items[0].get("price", {})
        if (not isinstance(price, dict) or price.get("id") != self.config.price_id
                or type(price.get("unit_amount")) is not int or price["unit_amount"] != self.config.unit_amount or price.get("currency") != self.config.currency
                or price.get("recurring", {}).get("interval") != "month" or type(price.get("recurring", {}).get("interval_count")) is not int or price["recurring"]["interval_count"] != 1
                or type(item.get("quantity")) is not int or item["quantity"] != 1):
            raise BillingInvalidRequest()
        end = stamp(item.get("current_period_end"))
        start = stamp(item.get("current_period_start"))
        if end <= start or end - start > timedelta(days=40) or type(snapshot.get("cancel_at_period_end")) is not bool:
            raise BillingInvalidRequest()
        paid_through = account["paid_through"]
        invoice = snapshot.get("latest_invoice")
        if isinstance(invoice, dict) and invoice.get("status") == "paid":
            parent = invoice.get("parent", {}).get("subscription_details", {}).get("subscription")
            lines = invoice.get("lines", {}).get("data", [])
            line = lines[0] if isinstance(lines, list) and len(lines) == 1 else {}
            details = line.get("parent", {}).get("subscription_item_details", {})
            pricing = line.get("pricing", {})
            paid_period = (line.get("period", {}).get("start") == int(start.timestamp())
                and line.get("period", {}).get("end") == int(end.timestamp())
                and line.get("parent", {}).get("type") == "subscription_item_details"
                and details.get("subscription") == snapshot["id"] and details.get("subscription_item") == item.get("id")
                and identifier(item.get("id"), "si") and details.get("proration") is False
                and pricing.get("type") == "price_details" and pricing.get("price_details", {}).get("price") == self.config.price_id
                and type(line.get("quantity")) is int and line["quantity"] == 1
                and type(line.get("amount")) is int and line["amount"] == self.config.unit_amount
                and line.get("currency") == self.config.currency)
            if (parent != snapshot["id"] or invoice.get("customer") != account["customer_id"]
                    or invoice.get("currency") != self.config.currency or invoice.get("livemode") is not False
                    or invoice.get("lines", {}).get("has_more") is True or not paid_period
                    or type(invoice.get("amount_paid")) is not int or invoice["amount_paid"] != self.config.unit_amount
                    or type(invoice.get("amount_due")) is not int or invoice["amount_due"] != self.config.unit_amount
                    or type(invoice.get("amount_remaining")) is not int or invoice["amount_remaining"] != 0):
                raise BillingInvalidRequest()
            paid_through = max(end, paid_through) if paid_through else end
        state = "unknown" if snapshot.get("pause_collection") else "ready"
        con.execute(update(accounts).where(accounts.c.project_id == project_id).values(subscription_id=snapshot["id"],
            status=snapshot["status"], sync_state=state, current_period_end=end,
            cancel_at_period_end=snapshot["cancel_at_period_end"], paid_through=paid_through, updated_at=utcnow()))
        return True

    def cancel(self, project_id, key, at_period_end, guard):
        with self.store.engine.begin() as con:
            guard(con)
            operation = self.operation(con, project_id, "cancel", key, {"at_period_end": at_period_end})
            if operation["status"] == "completed":
                return self.view(con, project_id)
            account = self.account(con, project_id)
            if not account["subscription_id"]:
                raise HTTPException(409, "No verified subscription is available to cancel")
            if not operation["result"]:
                operation["result"] = {"subscription_id": account["subscription_id"], "at_period_end": at_period_end}
                con.execute(update(operations).where(operations.c.id == operation["id"]).values(result=operation["result"]))
        error = False
        with self.store.engine.begin() as con:
            guard(con)
            current = con.execute(select(operations).where(operations.c.id == operation["id"])).mappings().one()
            if current["status"] == "completed":
                return self.view(con, project_id)
            account = self.account(con, project_id)
            target = current["result"]["subscription_id"]
            if account["subscription_id"] != target:
                raise HTTPException(409, "This cancellation belongs to an earlier subscription and cannot target its replacement")
            try:
                self.provider.cancel_subscription(target, "cancel:" + operation["id"], at_period_end=at_period_end)
                snapshot = self.provider.retrieve_subscription(target)
                self.apply_snapshot(con, project_id, snapshot)
                if (at_period_end and snapshot.get("cancel_at_period_end") is not True and snapshot.get("status") != "canceled") or (not at_period_end and snapshot.get("status") != "canceled"):
                    raise BillingInvalidRequest()
                con.execute(update(operations).where(operations.c.id == operation["id"]).values(status="completed", updated_at=utcnow()))
            except (BillingUnavailable, BillingInvalidRequest):
                con.execute(update(operations).where(operations.c.id == operation["id"]).values(status="unknown", updated_at=utcnow()))
                con.execute(update(accounts).where(accounts.c.project_id == project_id).values(sync_state="retrying", updated_at=utcnow()))
                error = True
            result = self.view(con, project_id)
        if error:
            raise HTTPException(503, "Cancellation is awaiting confirmation; retry the same Idempotency-Key.")
        return result

    def accept_event(self, event):
        if type(event.get("created")) is not int or not 0 <= event["created"] < 32503680000:
            raise HTTPException(400, "Webhook creation time exceeds supported bounds")
        kind = event["type"]
        if kind not in EVENT_TYPES:
            return {"accepted": False, "reason": "event_not_used"}
        obj = event["data"]["object"]
        customer = obj.get("customer")
        if not identifier(customer, "cus"):
            return {"accepted": False, "reason": "unrelated"}
        target_kind = "checkout" if kind.startswith("checkout.") else "subscription"
        if target_kind == "checkout" or kind.startswith("customer.subscription."):
            target = obj.get("id")
        else:
            parent = obj.get("parent")
            details = parent.get("subscription_details") if isinstance(parent, dict) else None
            target = details.get("subscription") if isinstance(details, dict) else None
        if not identifier(target, "cs" if target_kind == "checkout" else "sub"):
            raise HTTPException(400, "Webhook object relationship is unsupported")
        hashed = digest(event)
        with self.store.engine.begin() as con:
            # Event IDs are a separate idempotency domain from provider objects.
            from sqlalchemy import text
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "billing-event:" + event["id"]})
            prior = con.execute(select(events).where(events.c.event_id == event["id"])).mappings().first()
            if prior:
                if prior["payload_hash"] != hashed:
                    raise HTTPException(409, "Webhook event ID was reused with different content")
                return {"accepted": True, "duplicate": True, "status": prior["status"]}
            account = con.execute(select(accounts).where(accounts.c.customer_id == customer)).mappings().first()
            if not account:
                return {"accepted": False, "reason": "unrelated"}
            con.execute(select(projects.c.id).where(projects.c.id == account["project_id"]).with_for_update()).scalar_one()
            if target_kind == "checkout":
                known = con.execute(select(operations).where(operations.c.project_id == account["project_id"], operations.c.kind == "checkout")).mappings()
                if not any(row["result"] and row["result"].get("checkout_id") == target for row in known):
                    return {"accepted": False, "reason": "unknown_checkout"}
            if con.execute(select(func.count()).select_from(events).where(events.c.project_id == account["project_id"])).scalar_one() >= 10000:
                raise HTTPException(503, "Billing event capacity reached; operator reconciliation is required")
            now = utcnow()
            con.execute(insert(events).values(event_id=event["id"], project_id=account["project_id"], payload_hash=hashed,
                event_type=kind, target_id=target, target_kind=target_kind, provider_created=event["created"], status="pending",
                attempts=0, retry_at=now, created_at=now))
        return {"accepted": True, "duplicate": False, "status": "pending"}

    def reconcile(self, project_id, guard):
        error = False
        with self.store.engine.begin() as con:
            guard(con)
            account = self.account(con, project_id)
            try:
                target = account["subscription_id"]
                if not target:
                    latest = con.execute(select(operations).where(operations.c.project_id == project_id,
                        operations.c.kind == "checkout", operations.c.status == "completed").order_by(operations.c.created_at.desc()).limit(1)).mappings().first()
                    if not latest or not latest["result"]:
                        if not con.execute(select(operations.c.id).where(operations.c.project_id == project_id).limit(1)).first():
                            return self.view(con, project_id)
                        raise HTTPException(409, "Retry the original uncertain checkout before reconciling a subscription")
                    checkout = self.provider.retrieve_checkout(latest["result"]["checkout_id"])
                    if checkout.get("customer") != account["customer_id"] or checkout.get("livemode") is not False:
                        raise BillingInvalidRequest()
                    target = checkout.get("subscription")
                    if isinstance(target, dict):
                        target = target.get("id")
                    if checkout.get("status") == "expired" and target is None:
                        con.execute(update(operations).where(operations.c.id == latest["id"]).values(result={**latest["result"], "confirmed_expired": True}))
                        con.execute(update(accounts).where(accounts.c.project_id == project_id).values(status="none", sync_state="ready", updated_at=utcnow()))
                    elif not identifier(target, "sub"):
                        raise BillingUnavailable()
                if target:
                    self.apply_snapshot(con, project_id, self.provider.retrieve_subscription(target))
            except (BillingUnavailable, BillingInvalidRequest):
                con.execute(update(accounts).where(accounts.c.project_id == project_id).values(sync_state="retrying", updated_at=utcnow()))
                error = True
            result = self.view(con, project_id)
        if error:
            raise HTTPException(503, "Provider reconciliation is not yet available; no new access was granted")
        return result

    def process_one(self):
        if not self.enabled:
            return False
        now, lease = utcnow(), opaque_id()
        with self.store.engine.begin() as con:
            row = con.execute(select(events).where(or_(and_(events.c.status.in_(["pending", "retrying"]), events.c.retry_at <= now),
                and_(events.c.status == "processing", events.c.lease_until < now))).order_by(events.c.created_at).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if not row:
                return False
            row = dict(row)
            if row["attempts"] >= 5:
                con.execute(update(events).where(events.c.event_id == row["event_id"]).values(status="failed", processed_at=now))
                return True
            con.execute(update(events).where(events.c.event_id == row["event_id"]).values(status="processing", attempts=row["attempts"] + 1, lease_token=lease, lease_until=now + timedelta(seconds=120)))
        with self.store.engine.begin() as con:
            project = con.execute(select(projects.c.id).where(projects.c.id == row["project_id"]).with_for_update()).first()
            if not project:
                return True
            # Claimers use SKIP LOCKED: holding this row prevents lease theft
            # during provider reconciliation and the account projection commit.
            current = con.execute(select(events).where(events.c.event_id == row["event_id"]).with_for_update()).mappings().first()
            if not current or current["lease_token"] != lease:
                return True
            try:
                if row["target_kind"] == "checkout":
                    checkout = self.provider.retrieve_checkout(row["target_id"])
                    account = self.account(con, row["project_id"])
                    if checkout.get("customer") != account["customer_id"] or checkout.get("livemode") is not False:
                        raise BillingInvalidRequest()
                    subscription_id = checkout.get("subscription")
                    if isinstance(subscription_id, dict):
                        subscription_id = subscription_id.get("id")
                    if checkout.get("status") == "expired":
                        con.execute(update(accounts).where(accounts.c.project_id == row["project_id"], accounts.c.subscription_id.is_(None)).values(status="none", sync_state="ready", updated_at=utcnow()))
                        subscription_id = None
                    elif not identifier(subscription_id, "sub"):
                        raise BillingUnavailable()
                else:
                    subscription_id = row["target_id"]
                applied = True if subscription_id is None else self.apply_snapshot(con, row["project_id"], self.provider.retrieve_subscription(subscription_id))
                status = "processed" if applied else "ignored"
            except BillingInvalidRequest:
                con.execute(update(accounts).where(accounts.c.project_id == row["project_id"]).values(sync_state="unknown", updated_at=utcnow()))
                status = "failed"
            except BillingUnavailable:
                con.execute(update(accounts).where(accounts.c.project_id == row["project_id"]).values(sync_state="retrying", updated_at=utcnow()))
                status = "retrying" if row["attempts"] < 4 else "failed"
            result = con.execute(update(events).where(events.c.event_id == row["event_id"], events.c.lease_token == lease).values(
                status=status, lease_token=None, lease_until=None, retry_at=utcnow() + timedelta(seconds=30), processed_at=utcnow() if status in {"processed", "ignored", "failed"} else None))
            if result.rowcount != 1:
                raise RuntimeError("Billing reconciliation lease was lost")
        return True

    def check_deletion(self, con, project_id):
        account = self.account(con, project_id)
        if account["subscription_id"] and account["status"] not in TERMINAL:
            raise HTTPException(409, "Cancel the subscription immediately and confirm cancellation before deleting the project.")
        pending = con.execute(select(operations).where(operations.c.project_id == project_id, operations.c.kind == "checkout")).mappings()
        if any(row["status"] != "completed" or (row["result"] and not row["result"].get("confirmed_expired") and not account["subscription_id"]) for row in pending):
            raise HTTPException(409, "An open or uncertain checkout must expire or be reconciled before project deletion.")

    def health(self):
        queue = {key: 0 for key in ("pending", "processing", "retrying", "failed")}
        with self.store.engine.connect() as con:
            for status, count in con.execute(select(events.c.status, func.count()).where(events.c.status.in_(queue)).group_by(events.c.status)):
                queue[status] = min(int(count), 100000)
        state = "disabled" if not self.enabled else "failed" if queue["failed"] else "retrying" if queue["retrying"] else "ready"
        return {"mode": self.settings.billing_mode, "state": state, "queue": queue}


def configure_billing(app, settings, store, authorize, *, provider=None):
    service = BillingService(settings, store, provider)
    app.state.billing = service
    def owner(request, project_id):
        with store.engine.connect() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:billing")
    @app.get("/api/team/projects/{project_id}/billing")
    def billing(project_id: str, request: Request):
        with store.engine.connect() as con:
            authorize(request, con, project_id, minimum="owner", scope="owner:billing")
            return service.view(con, project_id)
    @app.post("/api/team/projects/{project_id}/billing/checkout")
    def checkout(project_id: str, body: CheckoutInput, request: Request):
        owner(request, project_id)
        service.require_enabled()
        if body.plan != "team":
            raise HTTPException(422, "Only the configured Team test plan is supported")
        guard = lambda con: authorize(request, con, project_id, minimum="owner", scope="owner:billing", lock=True)
        result = service.checkout(project_id, request.headers.get("idempotency-key", ""), guard)
        return {**result, "expires_at": stamp(result["expires_at"])}
    @app.post("/api/team/projects/{project_id}/billing/cancel")
    def cancel(project_id: str, body: CancelInput, request: Request):
        owner(request, project_id)
        service.require_enabled()
        guard = lambda con: authorize(request, con, project_id, minimum="owner", scope="owner:billing", lock=True)
        return service.cancel(project_id, request.headers.get("idempotency-key", ""), body.at_period_end, guard)
    @app.post("/api/team/projects/{project_id}/billing/reconcile")
    def reconcile(project_id: str, request: Request):
        owner(request, project_id)
        service.require_enabled()
        guard = lambda con: authorize(request, con, project_id, minimum="owner", scope="owner:billing", lock=True)
        return service.reconcile(project_id, guard)
    @app.post("/api/team/billing/webhook")
    async def webhook(request: Request):
        service.require_enabled()
        raw = await request.body()
        if len(raw) > 256 * 1024:
            raise HTTPException(413, "Billing webhook exceeds 256 KiB")
        try:
            event = service.provider.verify_event(raw, request.headers.get("stripe-signature", ""))
        except BillingInvalidRequest as exc:
            raise HTTPException(400, "Webhook signature, test mode or payload is invalid") from exc
        return service.accept_event(event)
    return service
