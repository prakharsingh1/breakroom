"""Stripe's real SDK runs against a recording transport, without credentials."""
import hashlib
import hmac
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import stripe

from breakroom_api.billing_provider import (
    BillingConfig, BillingInvalidRequest, BillingUnavailable, StripeProvider,
    MAX_WEBHOOK_BYTES, SDK_MAX_NETWORK_RETRIES, SDK_TIMEOUT_SECONDS,
    STRIPE_API_VERSION,
)


CONFIG = BillingConfig(mode="test", secret_key="sk_test_fixture_only",
    webhook_secret="whsec_fixture_only", price_id="price_fixture_monthly")


class RecordingTransport(stripe.HTTPClient):
    name = "breakroom-offline-fixture"

    def __init__(self):
        super().__init__()
        self.requests, self.responses = [], []

    def enqueue(self, value, status=200):
        self.responses.append((json.dumps(value), status, {"request-id": "req_fixture"}))

    def request(self, method, url, headers, post_data=None, **kwargs):
        self.requests.append({"method": method, "url": url, "headers": dict(headers),
                              "body": parse_qs(post_data or "")})
        if not self.responses:
            raise AssertionError("Unexpected SDK request; no network transport exists")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def _sleep_time_seconds(self, num_retries, *args, **kwargs):
        return 0


def provider_object(kind="subscription", object_id="sub_fixture", **changes):
    return {"id": object_id, "object": kind, "livemode": False, **changes}


def event_fixture(**changes):
    return {"id": "evt_fixture", "object": "event", "api_version": STRIPE_API_VERSION,
        "created": int(time.time()), "livemode": False, "type": "customer.subscription.updated",
        "data": {"object": provider_object()}, **changes}


def signed(raw, *, timestamp=None, secret=CONFIG.webhook_secret):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw,
                      hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class StripeProviderTests(unittest.TestCase):
    def setUp(self):
        self.transport = RecordingTransport()
        self.client = stripe.StripeClient(CONFIG.secret_key, http_client=self.transport,
            stripe_version=STRIPE_API_VERSION, max_network_retries=SDK_MAX_NETWORK_RETRIES)
        self.provider = StripeProvider(CONFIG, client=self.client)
        self.network_guard = patch("socket.create_connection", side_effect=AssertionError("Network forbidden"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def assert_request(self, method, path, *, key=None):
        request = self.transport.requests[-1]
        parsed = urlsplit(request["url"])
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("https", "api.stripe.com", path))
        self.assertEqual(request["method"], method)
        self.assertEqual(request["headers"]["Stripe-Version"], STRIPE_API_VERSION)
        self.assertEqual(request["headers"]["Authorization"], "Bearer " + CONFIG.secret_key)
        if key is not None:
            self.assertEqual(request["headers"]["Idempotency-Key"], key)
        return request

    def test_disabled_provider_never_constructs_or_calls_client(self):
        with patch("stripe.StripeClient", side_effect=AssertionError("Must remain disabled")):
            provider = StripeProvider(BillingConfig(), client=self.client)
        operations = [lambda: provider.verify_event(b"{}", "unused"),
            lambda: provider.create_customer("project_1", "customer:1"),
            lambda: provider.create_checkout("cus_fixture", "project_1", "x", "x", "checkout:1"),
            lambda: provider.retrieve_checkout("cs_test_fixture"),
            lambda: provider.retrieve_subscription("sub_fixture"),
            lambda: provider.cancel_subscription("sub_fixture", "cancel:1")]
        for operation in operations:
            with self.assertRaisesRegex(BillingUnavailable, "^Test billing is unavailable$"):
                operation()
        self.assertEqual(self.transport.requests, [])

    def test_config_requires_complete_test_credentials_and_rejects_live(self):
        CONFIG.validate()
        replace(CONFIG, secret_key="rk_test_restricted_fixture").validate()
        BillingConfig().validate()
        invalid = [replace(CONFIG, mode=mode) for mode in ("live", "production", "unknown", "", None)]
        invalid += [replace(CONFIG, **{name: None}) for name in ("secret_key", "webhook_secret", "price_id")]
        invalid += [replace(CONFIG, secret_key=key) for key in ("sk_live_secret", "rk_live_secret", "pk_test_public", "sk_test_", "sk_test_secret\n")]
        invalid += [BillingConfig(secret_key="sk_live_secret"), BillingConfig(price_id="price_partial"),
            replace(CONFIG, webhook_secret="not_a_signing_secret"), replace(CONFIG, price_id="arbitrary"),
            replace(CONFIG, unit_amount=True), replace(CONFIG, unit_amount=0),
            replace(CONFIG, unit_amount=49.0), replace(CONFIG, currency="USD")]
        for config in invalid:
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "^Invalid test billing configuration$"):
                config.validate()
        self.assertNotIn(CONFIG.secret_key, repr(CONFIG))
        self.assertNotIn(CONFIG.webhook_secret, repr(CONFIG))

    def test_config_origin_is_exact_and_secure_except_loopback(self):
        for origin in ("https://app.example.invalid", "http://127.0.0.1:3000", "http://[::1]:3000"):
            replace(CONFIG, public_origin=origin).validate()
        for origin in ("https://app.example.invalid/path", "https://app.example.invalid?query=1",
                "https://app.example.invalid#hash", "https://user:pass@app.example.invalid",
                "http://app.example.invalid", "https://localhost:bad", "https://local\nhost",
                "https://localhost\\evil", "//localhost:3000", ""):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                replace(CONFIG, public_origin=origin).validate()

    def test_default_client_has_pinned_api_bounded_timeout_retries_and_no_redirects(self):
        provider = StripeProvider(CONFIG)
        transport = provider.client._requestor._client
        self.assertEqual(transport._timeout, SDK_TIMEOUT_SECONDS)
        self.assertTrue(transport._verify_ssl_certs)
        self.assertFalse(transport._client.follow_redirects)
        self.assertEqual(provider.client._requestor._options.stripe_version, STRIPE_API_VERSION)
        self.assertEqual(provider.client._requestor._options.max_network_retries, 1)
        transport._client.close()

    def test_customer_creation_sends_only_server_project_metadata_and_operation_key(self):
        self.transport.enqueue(provider_object("customer", "cus_fixture"))
        value = self.provider.create_customer("project_1", "customer:project_1:1")
        self.assertIs(type(value), dict)
        request = self.assert_request("post", "/v1/customers", key="customer:project_1:1")
        self.assertEqual(request["body"], {"metadata[project_id]": ["project_1"]})

    def test_checkout_has_fixed_price_card_subscription_and_both_metadata_locations(self):
        self.transport.enqueue(provider_object("checkout.session", "cs_test_fixture",
            url="https://checkout.stripe.com/c/pay/cs_test_fixture"))
        value = self.provider.create_checkout("cus_fixture", "project_1",
            "http://localhost:3000/projects/project_1?checkout=success&session_id={CHECKOUT_SESSION_ID}",
            "http://localhost:3000/projects/project_1?checkout=cancelled", "checkout:project_1:1")
        self.assertEqual(value["id"], "cs_test_fixture")
        request = self.assert_request("post", "/v1/checkout/sessions", key="checkout:project_1:1")
        self.assertEqual(request["body"], {
            "customer": ["cus_fixture"], "mode": ["subscription"], "ui_mode": ["hosted_page"],
            "payment_method_types[0]": ["card"], "line_items[0][price]": [CONFIG.price_id],
            "line_items[0][quantity]": ["1"], "client_reference_id": ["project_1"],
            "metadata[project_id]": ["project_1"], "subscription_data[metadata][project_id]": ["project_1"],
            "success_url": ["http://localhost:3000/projects/project_1?checkout=success&session_id={CHECKOUT_SESSION_ID}"],
            "cancel_url": ["http://localhost:3000/projects/project_1?checkout=cancelled"],
        })

    def test_checkout_rejects_external_credential_and_malformed_return_urls_before_call(self):
        for bad in ("https://attacker.invalid/", "http://localhost:3001/projects", "//localhost:3000/",
                "http://user:secret@localhost:3000/projects", "http://localhost:3000/#hash",
                "http://localhost:3000\\@attacker.invalid/", "http://localhost:3000/\nheader"):
            for field in ("success_url", "cancel_url"):
                values = dict(customer_id="cus_fixture", project_id="project_1", operation_key="checkout:1",
                    success_url="http://localhost:3000/projects", cancel_url="http://localhost:3000/projects")
                values[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(BillingInvalidRequest):
                    self.provider.create_checkout(**values)
        self.assertEqual(self.transport.requests, [])

    def test_checkout_and_subscription_retrieval_and_expansion(self):
        self.transport.enqueue(provider_object("checkout.session", "cs_test_fixture"))
        self.provider.retrieve_checkout("cs_test_fixture")
        self.assert_request("get", "/v1/checkout/sessions/cs_test_fixture")
        nested = provider_object(items={"object": "list", "data": [{"id": "si_fixture", "quantity": 1,
            "current_period_start": 100, "current_period_end": 200,
            "price": {"id": CONFIG.price_id, "unit_amount": 4900, "currency": "usd",
                      "recurring": {"interval": "month", "interval_count": 1}}}]},
            latest_invoice={"id": "in_fixture", "status": "paid"})
        self.transport.enqueue(nested)
        self.assertEqual(self.provider.retrieve_subscription("sub_fixture"), nested)
        request = self.assert_request("get", "/v1/subscriptions/sub_fixture")
        self.assertEqual(parse_qs(urlsplit(request["url"]).query), {"expand[0]": ["latest_invoice"], "expand[1]": ["items.data.price"]})

    def test_cancel_at_period_end_and_immediate_are_explicit(self):
        self.transport.enqueue(provider_object(cancel_at_period_end=True))
        self.provider.cancel_subscription("sub_fixture", "cancel:end:1")
        request = self.assert_request("post", "/v1/subscriptions/sub_fixture", key="cancel:end:1")
        self.assertEqual(request["body"], {"cancel_at_period_end": ["true"]})
        self.transport.enqueue(provider_object(status="canceled"))
        self.provider.cancel_subscription("sub_fixture", "cancel:now:1", at_period_end=False)
        request = self.assert_request("delete", "/v1/subscriptions/sub_fixture", key="cancel:now:1")
        # Stripe encodes DELETE parameters in its query string.
        params = request["body"] or parse_qs(urlsplit(request["url"]).query)
        self.assertEqual(params, {"invoice_now": ["false"], "prorate": ["false"]})

    def test_invalid_identifiers_and_operation_keys_never_reach_transport(self):
        bad_operations = [lambda: self.provider.retrieve_checkout("cs_live_secret"),
            lambda: self.provider.retrieve_subscription("sub_x/../../secrets"),
            lambda: self.provider.retrieve_subscription("https://attacker.invalid"),
            lambda: self.provider.cancel_subscription("sub_fixture", "cancel:1", at_period_end="false"),
            lambda: self.provider.create_customer("project\nsecret", "op"),
            lambda: self.provider.create_customer("project_1", "x" * 256),
            lambda: self.provider.create_customer("project_1", "secret\r\nheader: injected")]
        for operation in bad_operations:
            with self.assertRaises(BillingInvalidRequest):
                operation()
        self.assertEqual(self.transport.requests, [])

    def test_sdk_retry_is_bounded_and_keeps_application_idempotency_key(self):
        self.transport.enqueue({"error": {"type": "api_error", "message": "fixture failure"}}, 503)
        self.transport.enqueue(provider_object("customer", "cus_fixture"))
        self.provider.create_customer("project_1", "customer:stable")
        self.assertEqual(len(self.transport.requests), 2)
        self.assertEqual([r["headers"]["Idempotency-Key"] for r in self.transport.requests], ["customer:stable"] * 2)
        for _ in range(2):
            self.transport.responses.append(stripe.APIConnectionError("private connection context", should_retry=True))
        with self.assertRaisesRegex(BillingUnavailable, "^Test billing is unavailable$"):
            self.provider.create_customer("project_1", "customer:failed")
        self.assertEqual(len(self.transport.requests), 4)

    def test_provider_errors_expose_only_fixed_safe_strings(self):
        for status, kind, expected in ((400, "invalid_request_error", BillingInvalidRequest),
                (401, "invalid_request_error", BillingUnavailable), (403, "invalid_request_error", BillingUnavailable),
                (429, "rate_limit_error", BillingUnavailable), (503, "api_error", BillingUnavailable)):
            with self.subTest(status=status):
                error = {"error": {"type": kind, "message": "sk_live_DO_NOT_REFLECT customer_secret", "param": "secret"}}
                self.transport.enqueue(error, status)
                if status == 503:
                    self.transport.enqueue(error, status)
                with self.assertRaises(expected) as result:
                    self.provider.create_customer("project_1", "customer:error")
                self.assertNotIn("secret", str(result.exception))
                self.assertNotIn("sk_live", repr(result.exception))
                self.assertEqual(self.transport.responses, [])
        self.assertEqual(str(BillingInvalidRequest("sensitive detail")), "Invalid billing request")
        self.assertEqual(str(BillingUnavailable("sensitive detail")), "Test billing is unavailable")

    def test_provider_rejects_live_or_malformed_success_responses(self):
        for response in (provider_object("customer", "cus_live", livemode=True),
                {"id": "cus_missing_mode", "object": "customer"},
                provider_object("subscription", "sub_wrong")):
            self.transport.enqueue(response)
            with self.assertRaises(BillingUnavailable):
                self.provider.create_customer("project_1", "customer:bad-response")
        self.transport.responses.append(("not json", 200, {}))
        with self.assertRaises(BillingUnavailable):
            self.provider.create_customer("project_1", "customer:invalid-json")

    def test_valid_signed_raw_event_roundtrips_and_multiple_signatures_allow_rotation(self):
        event = event_fixture()
        raw = json.dumps(event, indent=2).encode()
        header = signed(raw)
        self.assertEqual(self.provider.verify_event(raw, header), event)
        self.assertEqual(self.provider.verify_event(raw, header + ",v1=" + "0" * 64), event)
        self.assertEqual(self.transport.requests, [])

    def test_signature_tampering_missing_wrong_secret_and_old_versions_are_rejected(self):
        raw = json.dumps(event_fixture(), indent=2).encode()
        signature = signed(raw)
        invalid = [(raw, ""), (raw, "garbage"), (raw, "t=not-an-integer,v1=signature"),
            (raw, signature.replace("v1=", "v0=")), (raw, signed(raw, secret="whsec_wrong")),
            (raw + b" ", signature), (json.dumps(json.loads(raw), separators=(",", ":")).encode(), signature)]
        for payload, header in invalid:
            with self.subTest(header=header), self.assertRaisesRegex(BillingInvalidRequest, "^Invalid billing request$"):
                self.provider.verify_event(payload, header)

    def test_stale_and_excessively_future_signed_events_are_rejected(self):
        raw = json.dumps(event_fixture()).encode()
        now = int(time.time())
        for timestamp in (now - 600, now + 600):
            with self.subTest(timestamp=timestamp), self.assertRaises(BillingInvalidRequest):
                self.provider.verify_event(raw, signed(raw, timestamp=timestamp))
        self.provider.verify_event(raw, signed(raw, timestamp=now + 299))

    def test_signed_live_wrong_version_and_invalid_event_shapes_are_rejected(self):
        invalid = [event_fixture(livemode=value) for value in (True, None, 0, "false")]
        invalid += [event_fixture(api_version="2020-08-27"), event_fixture(object="v2.core.event"),
            event_fixture(id="unknown"), event_fixture(id=1), event_fixture(type=None),
            event_fixture(created=True), event_fixture(created=-1), event_fixture(data=[]),
            event_fixture(data={"object": []}), event_fixture(data={"object": {"livemode": True}})]
        for event in invalid:
            raw = json.dumps(event).encode()
            with self.subTest(event=event), self.assertRaises(BillingInvalidRequest):
                self.provider.verify_event(raw, signed(raw))

    def test_signed_duplicate_nonfinite_deep_and_non_object_json_are_rejected(self):
        valid = json.dumps(event_fixture()).encode()
        invalid = [b"[]", b"null", b"{", b"\xff", valid[:-1] + b',"livemode":false}',
            valid[:-1] + b',"extra":' + b"[" * 65 + b"0" + b"]" * 65 + b"}",
            valid[:-1] + b',"extra":{"duplicate":1,"duplicate":2}}']
        invalid += [valid[:-1] + b',"extra":' + token + b"}" for token in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999")]
        for raw in invalid:
            with self.subTest(raw=raw[:80]), self.assertRaises(BillingInvalidRequest):
                self.provider.verify_event(raw, signed(raw))

    def test_webhook_size_signature_size_and_node_count_are_bounded(self):
        valid = json.dumps(event_fixture()).encode()
        excessive_nodes = valid[:-1] + b',"extra":[' + b"0," * 50_000 + b"0]}"
        for raw, signature in ((b"x" * (MAX_WEBHOOK_BYTES + 1), "header"),
                (valid, "x" * 4097), (excessive_nodes, signed(excessive_nodes))):
            with self.assertRaises(BillingInvalidRequest):
                self.provider.verify_event(raw, signature)


if __name__ == "__main__":
    unittest.main()
