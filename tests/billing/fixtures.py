"""Synthetic provider state; signatures still use the installed Stripe SDK."""
import copy
import hashlib
import hmac
import json
import time
import stripe
from breakroom_api.billing_provider import BillingUnavailable, StripeProvider, STRIPE_API_VERSION


class NeverNetwork(stripe.HTTPClient):
    name = "offline-billing-fixture"
    def request(self, *args, **kwargs):
        raise AssertionError("No external billing call is allowed in these tests")


def subscription(customer, *, subscription_id="sub_fixture", status="active", paid=True):
    now = int(time.time())
    price = {"id": "price_fixture", "unit_amount": 4900, "currency": "usd", "recurring": {"interval": "month", "interval_count": 1}}
    period = {"start": now - 60, "end": now + 30 * 86400}
    line = {"id": "il_fixture", "amount": 4900, "currency": "usd", "quantity": 1, "period": period,
        "parent": {"type": "subscription_item_details", "subscription_item_details": {"subscription": subscription_id, "subscription_item": "si_fixture", "proration": False}},
        "pricing": {"type": "price_details", "price_details": {"price": "price_fixture"}}}
    invoice = {"id": "in_fixture", "object": "invoice", "customer": customer, "livemode": False,
        "currency": "usd", "status": "paid" if paid else "open", "amount_paid": 4900 if paid else 0,
        "amount_due": 4900, "amount_remaining": 0 if paid else 4900,
        "parent": {"type": "subscription_details", "subscription_details": {"subscription": subscription_id}},
        "lines": {"data": [line], "has_more": False}}
    return {"id": subscription_id, "object": "subscription", "customer": customer, "livemode": False,
        "status": status, "cancel_at_period_end": False, "pause_collection": None,
        "items": {"data": [{"id": "si_fixture", "quantity": 1, "price": price,
            "current_period_start": period["start"], "current_period_end": period["end"]}], "has_more": False},
        "latest_invoice": invoice}


class FakeProvider(StripeProvider):
    def __init__(self, config):
        super().__init__(config, client=stripe.StripeClient(config.secret_key, http_client=NeverNetwork(), stripe_version=STRIPE_API_VERSION, max_network_retries=0))
        self.customers, self.checkouts, self.snapshots, self.operation_cache = {}, {}, {}, {}
        self.cancel_calls, self.retrieve_calls = [], []
        self.lose_checkout_once = False
        self.lose_cancel_once = False
        self.unavailable = False
        self.retrieve_hook = None
        self.event_created = int(time.time())

    def create_customer(self, project_id, operation_key):
        if operation_key not in self.operation_cache:
            value = {"id": "cus_fixture_" + str(len(self.customers) + 1), "object": "customer", "livemode": False}
            self.customers[value["id"]] = value
            self.operation_cache[operation_key] = value
        return copy.deepcopy(self.operation_cache[operation_key])

    def create_checkout(self, customer_id, project_id, success_url, cancel_url, operation_key):
        if operation_key not in self.operation_cache:
            sid = "cs_test_fixture_" + str(len(self.checkouts) + 1)
            value = {"id": sid, "object": "checkout.session", "customer": customer_id, "livemode": False,
                "mode": "subscription", "status": "open", "subscription": None, "expires_at": int(time.time()) + 3600,
                "url": "https://checkout.stripe.com/c/pay/" + sid}
            self.checkouts[sid] = value
            self.operation_cache[operation_key] = value
        if self.lose_checkout_once:
            self.lose_checkout_once = False
            raise BillingUnavailable()
        return copy.deepcopy(self.operation_cache[operation_key])

    def complete(self, *, subscription_id="sub_fixture", status="active", paid=True):
        session = list(self.checkouts.values())[-1]
        session["status"], session["subscription"] = "complete", subscription_id
        value = subscription(session["customer"], subscription_id=subscription_id, status=status, paid=paid)
        self.snapshots[subscription_id] = value
        return value

    def retrieve_checkout(self, session_id):
        if self.unavailable:
            raise BillingUnavailable()
        return copy.deepcopy(self.checkouts[session_id])

    def retrieve_subscription(self, subscription_id):
        self.retrieve_calls.append(subscription_id)
        if self.retrieve_hook:
            self.retrieve_hook()
        if self.unavailable:
            raise BillingUnavailable()
        return copy.deepcopy(self.snapshots[subscription_id])

    def cancel_subscription(self, subscription_id, operation_key, at_period_end=True):
        if operation_key not in self.operation_cache:
            self.cancel_calls.append((subscription_id, operation_key, at_period_end))
            value = self.snapshots[subscription_id]
            value["cancel_at_period_end"] = at_period_end
            if not at_period_end:
                value["status"] = "canceled"
            self.operation_cache[operation_key] = copy.deepcopy(value)
        if self.lose_cancel_once:
            self.lose_cancel_once = False
            raise BillingUnavailable()
        return copy.deepcopy(self.operation_cache[operation_key])

    def event(self, event_id="evt_fixture", kind="customer.subscription.updated", *, object_override=None, created=None):
        obj = object_override or list(self.snapshots.values())[-1]
        if kind.startswith("invoice.") and object_override is None:
            obj = obj["latest_invoice"]
        if kind.startswith("checkout.") and object_override is None:
            obj = list(self.checkouts.values())[-1]
        event = {"id": event_id, "object": "event", "api_version": STRIPE_API_VERSION,
            "created": self.event_created if created is None else created, "livemode": False, "type": kind, "data": {"object": copy.deepcopy(obj)}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        stamp = int(time.time())
        signature = hmac.new(self.config.webhook_secret.encode(), str(stamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
        return raw, f"t={stamp},v1={signature}"
