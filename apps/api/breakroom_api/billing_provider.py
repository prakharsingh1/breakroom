"""Replaceable, test-only Stripe boundary; no credentials means no requests.

Uses stripe 15.6.1's V1 APIs and verified snapshot webhooks. Project ownership,
durable operation/event deduplication, and entitlements belong to the service.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

import stripe


STRIPE_API_VERSION = "2026-08-26.dahlia"
SDK_TIMEOUT_SECONDS = 8.0
SDK_MAX_NETWORK_RETRIES = 1
WEBHOOK_TOLERANCE_SECONDS = 300
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024
_INVALID = "Invalid billing request"
_UNAVAILABLE = "Test billing is unavailable"
_CONFIG_ERROR = "Invalid test billing configuration"


class BillingUnavailable(Exception):
    def __init__(self, _detail=None):
        super().__init__(_UNAVAILABLE)


class BillingInvalidRequest(Exception):
    def __init__(self, _detail=None):
        super().__init__(_INVALID)


def _origin(value: str) -> tuple[str, str, int | None]:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\\]", value):
        raise ValueError(_CONFIG_ERROR)
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError(_CONFIG_ERROR)
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(_CONFIG_ERROR)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


@dataclass(frozen=True)
class BillingConfig:
    mode: str = "disabled"
    secret_key: str | None = field(default=None, repr=False)
    webhook_secret: str | None = field(default=None, repr=False)
    price_id: str | None = None
    public_origin: str = "http://localhost:3000"
    unit_amount: int = 4900
    currency: str = "usd"

    def validate(self) -> None:
        try:
            if self.mode not in {"disabled", "test"}:
                raise ValueError
            _origin(self.public_origin)
            parsed = urlsplit(self.public_origin)
            if parsed.path or parsed.query or parsed.fragment:
                raise ValueError
            if type(self.unit_amount) is not int or not 1 <= self.unit_amount <= 99_999_999:
                raise ValueError
            if not isinstance(self.currency, str) or not re.fullmatch(r"[a-z]{3}", self.currency):
                raise ValueError
            credentials = (self.secret_key, self.webhook_secret, self.price_id)
            if self.mode == "test" or any(value is not None for value in credentials):
                for value, pattern in zip(credentials, (r"(?:sk|rk)_test_[A-Za-z0-9_]+", r"whsec_[A-Za-z0-9_]+", r"price_[A-Za-z0-9_]+")):
                    if not isinstance(value, str) or len(value) > 512 or not re.fullmatch(pattern, value):
                        raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError(_CONFIG_ERROR) from None


def _identifier(value: str, prefix: str = "", max_length: int = 200) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= max_length
            or not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]+", value)):
        raise BillingInvalidRequest()
    return value


def _operation_key(value: str) -> dict:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", value):
        raise BillingInvalidRequest()
    return {"idempotency_key": value}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError


def _strict_event_json(raw: bytes) -> dict:
    value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    stack, count = [(value, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 64 or count > 50_000 or (isinstance(node, float) and not math.isfinite(node)):
            raise ValueError
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
    if not isinstance(value, dict):
        raise ValueError
    return value


class StripeProvider:
    def __init__(self, config: BillingConfig, client=None):
        config.validate()
        self.config = config
        # A disabled provider never even constructs a transport.
        self.client = None
        if config.mode == "test":
            self.client = client if client is not None else stripe.StripeClient(
                config.secret_key, stripe_version=STRIPE_API_VERSION,
                max_network_retries=SDK_MAX_NETWORK_RETRIES,
                http_client=stripe.HTTPXClient(timeout=SDK_TIMEOUT_SECONDS, allow_sync_methods=True),
            )

    def _enabled(self):
        if self.config.mode != "test" or self.client is None:
            raise BillingUnavailable()
        return self.client

    def _call(self, operation: Callable, *, object_type: str) -> dict:
        try:
            value = operation().to_dict()
            if (not isinstance(value, dict) or value.get("object") != object_type
                    or value.get("livemode") is not False or not isinstance(value.get("id"), str)):
                raise ValueError
            return value
        except (stripe.InvalidRequestError, stripe.CardError):
            raise BillingInvalidRequest() from None
        except (stripe.StripeError, ValueError, TypeError, AttributeError, RecursionError):
            raise BillingUnavailable() from None

    def verify_event(self, rawbytes: bytes, signature: str) -> dict:
        client = self._enabled()
        try:
            if (not isinstance(rawbytes, bytes) or not 1 <= len(rawbytes) <= MAX_WEBHOOK_BYTES
                    or not isinstance(signature, str) or not 1 <= len(signature) <= 4096):
                raise ValueError
            decoded = _strict_event_json(rawbytes)
            # The maintained SDK authenticates the exact bytes, including whitespace.
            event = client.construct_event(rawbytes, signature, self.config.webhook_secret,
                tolerance=WEBHOOK_TOLERANCE_SECONDS).to_dict()
            # Stripe 15.6.1 rejects old signatures, but accepts future timestamps.
            # Match its first timestamp selection after its cryptographic check.
            timestamp = int(next(part[2:] for part in signature.split(",") if part.startswith("t=")))
            if timestamp > time.time() + WEBHOOK_TOLERANCE_SECONDS:
                raise ValueError
            if (event != decoded or event.get("object") != "event" or event.get("livemode") is not False
                    or event.get("api_version") != STRIPE_API_VERSION
                    or not re.fullmatch(r"evt_[A-Za-z0-9_]{1,196}", event.get("id", ""))
                    or not re.fullmatch(r"[a-z][a-z0-9_.]{1,199}", event.get("type", ""))
                    or type(event.get("created")) is not int or event["created"] < 0
                    or not isinstance(event.get("data"), dict)
                    or not isinstance(event["data"].get("object"), dict)):
                raise ValueError
            if event["data"]["object"].get("livemode", False) is not False:
                raise ValueError
            return event
        except (stripe.StripeError, ValueError, TypeError, AttributeError, RecursionError, StopIteration):
            raise BillingInvalidRequest() from None

    def create_customer(self, project_id: str, operation_key: str) -> dict:
        client = self._enabled()
        metadata = {"project_id": _identifier(project_id)}
        options = _operation_key(operation_key)
        return self._call(lambda: client.v1.customers.create({"metadata": metadata}, options=options), object_type="customer")

    def _return_url(self, value: str) -> str:
        try:
            if _origin(value) != _origin(self.config.public_origin) or urlsplit(value).fragment:
                raise ValueError
            return value
        except (ValueError, TypeError, AttributeError):
            raise BillingInvalidRequest() from None

    def create_checkout(self, customer_id: str, project_id: str, success_url: str,
                        cancel_url: str, operation_key: str) -> dict:
        client = self._enabled()
        project_id = _identifier(project_id)
        params = {
            "customer": _identifier(customer_id, "cus_"),
            "mode": "subscription", "ui_mode": "hosted_page",
            "payment_method_types": ["card"],
            "line_items": [{"price": self.config.price_id, "quantity": 1}],
            "client_reference_id": project_id,
            "metadata": {"project_id": project_id},
            "subscription_data": {"metadata": {"project_id": project_id}},
            "success_url": self._return_url(success_url),
            "cancel_url": self._return_url(cancel_url),
        }
        options = _operation_key(operation_key)
        return self._call(lambda: client.v1.checkout.sessions.create(params, options=options), object_type="checkout.session")

    def retrieve_checkout(self, session_id: str) -> dict:
        client = self._enabled()
        session_id = _identifier(session_id, "cs_test_")
        return self._call(lambda: client.v1.checkout.sessions.retrieve(session_id), object_type="checkout.session")

    def retrieve_subscription(self, subscription_id: str) -> dict:
        client = self._enabled()
        subscription_id = _identifier(subscription_id, "sub_")
        return self._call(lambda: client.v1.subscriptions.retrieve(subscription_id,
            {"expand": ["latest_invoice", "items.data.price"]}), object_type="subscription")

    def cancel_subscription(self, subscription_id: str, operation_key: str,
                            at_period_end: bool = True) -> dict:
        client = self._enabled()
        subscription_id = _identifier(subscription_id, "sub_")
        options = _operation_key(operation_key)
        if type(at_period_end) is not bool:
            raise BillingInvalidRequest()
        if at_period_end:
            return self._call(lambda: client.v1.subscriptions.update(subscription_id,
                {"cancel_at_period_end": True}, options=options), object_type="subscription")
        # DELETE is intrinsically idempotent at Stripe; local operation records
        # remain responsible for durable retries, including a repeated 404.
        return self._call(lambda: client.v1.subscriptions.cancel(subscription_id,
            {"invoice_now": False, "prorate": False}, options=options), object_type="subscription")
