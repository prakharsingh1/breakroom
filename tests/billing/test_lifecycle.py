"""Real PostgreSQL lifecycle/authorization tests with signed offline Stripe fixtures."""
import copy
import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from breakroom.agents import corrected
from breakroom.evaluator import run_in_process
from breakroom.scenarios import load_case
from breakroom.uploads import prepare_upload
from breakroom_api.billing_db import accounts, events, operations
from breakroom_api.billing_provider import BillingConfig
from breakroom_api.team import create_app
from breakroom_api.team_config import TeamSettings
from breakroom_api.team_db import memberships, reports, utcnow
from fixtures import FakeProvider


class BillingLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upload = prepare_upload(run_in_process(load_case("normal-refund"), corrected), redaction_key=b"q" * 32)

    def setUp(self):
        self.settings = TeamSettings(database_url=os.environ.get("BREAKROOM_TEAM_TEST_DATABASE_URL", TeamSettings.database_url),
            database_schema="test_billing_" + uuid.uuid4().hex, environment="test", dev_login=True,
            public_origin="http://127.0.0.1:3000", rate_limit_per_minute=10000, billing_mode="test",
            stripe_secret_key="sk_test_fixture", stripe_webhook_secret="whsec_fixture", stripe_price_id="price_fixture",
            billing_worker_seconds=60, billing_report_limit=2, billing_seat_limit=3)
        config = BillingConfig(mode="test", secret_key="sk_test_fixture", webhook_secret="whsec_fixture",
            price_id="price_fixture", public_origin=self.settings.public_origin)
        self.provider = FakeProvider(config)
        self.app = create_app(self.settings, billing_provider=self.provider)
        self.store, self.service = self.app.state.store, self.app.state.billing
        self.client = TestClient(self.app, base_url=self.settings.public_origin, client=("127.0.0.1", 8000))
        self.client.__enter__()
        result = self.client.post("/api/team/auth/dev-login", json={"email": "owner@example.invalid", "display_name": "Owner"}, headers={"Origin": self.settings.public_origin})
        self.assertEqual(result.status_code, 200, result.text)
        self.user = result.json()["user"]
        self.headers = {"Origin": self.settings.public_origin, "X-CSRF-Token": self.client.get("/api/team/me").json()["csrf_token"]}
        self.project = self.client.post("/api/team/projects", json={"name": "Billing fixture"}, headers=self.headers).json()["id"]
        self.base = "/api/team/projects/" + self.project

    def tearDown(self):
        self.client.__exit__(None, None, None)
        with self.store.engine.begin() as con:
            con.execute(text("DROP SCHEMA " + self.settings.database_schema + " CASCADE"))
        self.store.engine.dispose()

    def checkout(self, key="a" * 64, **body):
        return self.client.post(self.base + "/billing/checkout", json={"plan": "team", **body}, headers={**self.headers, "Idempotency-Key": key})

    def state(self):
        response = self.client.get(self.base + "/billing")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def deliver(self, event_id="evt_fixture", kind="customer.subscription.updated", **kwargs):
        raw, signature = self.provider.event(event_id, kind, **kwargs)
        return self.client.post("/api/team/billing/webhook", content=raw, headers={"Content-Type": "application/json", "Stripe-Signature": signature})

    def activate(self):
        response = self.checkout()
        self.assertEqual(response.status_code, 200, response.text)
        self.provider.complete()
        self.assertEqual(self.deliver().status_code, 200)
        self.assertTrue(self.service.process_one())
        self.assertEqual(self.state()["access"], "paid")

    def import_report(self, key="b" * 64, client=None, headers=None):
        return (client or self.client).post(self.base + "/reports", json=self.upload,
            headers={**(headers or self.headers), "Idempotency-Key": key})

    def cancel(self, key="c" * 64, at_period_end=True):
        return self.client.post(self.base + "/billing/cancel", json={"at_period_end": at_period_end}, headers={**self.headers, "Idempotency-Key": key})

    def other_user(self, email, role=None):
        client = TestClient(self.app, base_url=self.settings.public_origin, client=("127.0.0.1", 8001))
        user = client.post("/api/team/auth/dev-login", json={"email": email, "display_name": "Fixture"}, headers={"Origin": self.settings.public_origin}).json()["user"]
        if role:
            response = self.client.post(self.base + "/members", json={"email": email, "role": role}, headers=self.headers)
            self.assertEqual(response.status_code, 201, response.text)
        headers = {"Origin": self.settings.public_origin, "X-CSRF-Token": client.get("/api/team/me").json()["csrf_token"]}
        return client, user, headers

    def test_checkout_and_success_return_do_not_grant_access(self):
        response = self.checkout()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["test_mode"])
        self.assertIsInstance(response.json()["expires_at"], str)
        self.client.get(self.base + "?billing=returned&payment_status=paid")
        self.assertEqual(self.state()["access"], "read_only")
        self.assertEqual(self.import_report().status_code, 402)
        self.assertEqual(self.checkout().json()["checkout_id"], response.json()["checkout_id"])
        self.assertEqual(len(self.provider.checkouts), 1)
        self.assertEqual(self.checkout(key="d" * 64).status_code, 409)

    def test_browser_cannot_select_price_amount_or_return_origin(self):
        for key in ("price_id", "amount_minor", "success_url", "currency"):
            self.assertEqual(self.checkout(**{key: "attacker"}).status_code, 422)
        self.assertEqual(self.checkout(plan="other").status_code, 422)
        self.assertEqual(self.provider.checkouts, {})

    def test_lost_checkout_response_recovers_same_operation_only(self):
        self.provider.lose_checkout_once = True
        self.assertEqual(self.checkout().status_code, 503)
        self.assertEqual(self.checkout(key="d" * 64).status_code, 409)
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual((len(self.provider.customers), len(self.provider.checkouts)), (1, 1))
        self.assertEqual(self.state()["access"], "read_only")

    def test_paid_invoice_and_current_subscription_grant_access(self):
        self.activate()
        result = self.import_report()
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(self.state()["usage"]["reports"], 1)
        self.assertGreater(self.state()["usage"]["storage_bytes"], 0)

    def test_active_without_paid_invoice_and_trial_do_not_unlock(self):
        self.checkout()
        for index, (status, paid) in enumerate((("active", False), ("trialing", True), ("incomplete", False), ("past_due", False), ("unpaid", False))):
            self.provider.complete(status=status, paid=paid)
            self.deliver("evt_state_" + str(index))
            self.service.process_one()
            self.assertEqual(self.state()["access"], "read_only", status)

    def test_webhook_signature_and_duplicate_conflict(self):
        self.checkout()
        self.provider.complete()
        raw, signature = self.provider.event()
        rejected = self.client.post("/api/team/billing/webhook", content=raw + b" ", headers={"Content-Type": "application/json", "Stripe-Signature": signature})
        self.assertEqual(rejected.status_code, 400)
        first, second = self.deliver(), self.deliver()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertTrue(second.json()["duplicate"])
        self.provider.snapshots["sub_fixture"]["status"] = "past_due"
        self.assertEqual(self.deliver().status_code, 409)
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(func.count()).select_from(events)).scalar_one(), 1)

    def test_same_second_reordered_events_use_current_provider_state(self):
        self.activate()
        active = copy.deepcopy(self.provider.snapshots["sub_fixture"])
        self.provider.snapshots["sub_fixture"]["status"] = "canceled"
        created = int(time.time())
        self.assertEqual(self.deliver("evt_cancel", "customer.subscription.deleted", created=created).status_code, 200)
        self.assertEqual(self.deliver("evt_old_active", object_override=active, created=created).status_code, 200)
        self.service.process_one()
        self.service.process_one()
        self.assertEqual(self.state()["status"], "canceled")
        self.assertEqual(self.state()["access"], "read_only")

    def test_malformed_paid_invoice_relationships_reject_without_500(self):
        self.checkout()
        self.provider.complete()
        for index, value in enumerate((None, [], 1, {"subscription_details": None}, {"subscription_details": []})):
            invoice = copy.deepcopy(self.provider.snapshots["sub_fixture"]["latest_invoice"])
            invoice["parent"] = value
            self.assertEqual(self.deliver("evt_bad_parent_" + str(index), "invoice.paid", object_override=invoice).status_code, 400)

    def test_paid_period_requires_correct_line_price_item_amount_and_integers(self):
        self.checkout()
        valid = self.provider.complete()
        mutations = [
            lambda value: value["latest_invoice"]["lines"]["data"][0]["parent"]["subscription_item_details"].update(subscription="sub_other"),
            lambda value: value["latest_invoice"]["lines"]["data"][0]["parent"]["subscription_item_details"].update(subscription_item="si_other"),
            lambda value: value["latest_invoice"]["lines"]["data"][0]["pricing"]["price_details"].update(price="price_other"),
            lambda value: value["latest_invoice"]["lines"]["data"][0].update(amount=1),
            lambda value: value["latest_invoice"].update(amount_paid=0),
            lambda value: value["latest_invoice"].update(amount_due=4900.0),
            lambda value: value["latest_invoice"].update(amount_remaining=False),
            lambda value: value["items"]["data"][0]["price"].update(unit_amount=4900.0),
            lambda value: value["items"]["data"][0]["price"]["recurring"].update(interval_count=True),
            lambda value: value.update(items=[]),
            lambda value: value.update(status=[]),
            lambda value: value["latest_invoice"].update(parent=None),
        ]
        for index, mutate in enumerate(mutations):
            snapshot = copy.deepcopy(valid)
            mutate(snapshot)
            self.provider.snapshots["sub_fixture"] = snapshot
            self.deliver("evt_invalid_snapshot_" + str(index), object_override=valid)
            self.assertTrue(self.service.process_one())
            self.assertEqual(self.state()["access"], "read_only", index)
            self.assertEqual(self.state()["sync_state"], "unknown", index)

    def test_provider_unavailability_retries_and_health_is_visible(self):
        self.checkout()
        self.provider.complete()
        self.deliver()
        self.provider.unavailable = True
        self.service.process_one()
        self.assertEqual(self.state()["access"], "read_only")
        self.assertEqual(self.client.get("/api/team/health").json()["billing"]["state"], "retrying")
        with self.store.engine.begin() as con:
            con.execute(update(events).values(retry_at=utcnow() - timedelta(seconds=1)))
        self.provider.unavailable = False
        self.service.process_one()
        self.assertEqual(self.state()["access"], "paid")

    def test_cancel_at_period_end_keeps_paid_period_immediate_revokes(self):
        self.activate()
        self.import_report()
        response = self.cancel()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["cancel_at_period_end"])
        self.assertEqual(response.json()["access"], "paid")
        self.assertEqual(self.cancel().status_code, 200)
        self.assertEqual(len(self.provider.cancel_calls), 1)
        self.assertEqual(self.cancel(at_period_end=False).status_code, 409)
        self.assertEqual(self.client.delete(self.base, headers=self.headers).status_code, 409)
        self.assertEqual(self.cancel(key="d" * 64, at_period_end=False).status_code, 200)
        self.assertEqual(self.state()["access"], "read_only")
        self.assertEqual(self.import_report(key="e" * 64).status_code, 402)
        self.assertEqual(self.client.get(self.base + "/reports").status_code, 200)
        self.assertEqual(self.client.delete(self.base, headers=self.headers).status_code, 200)
        with self.store.engine.connect() as con:
            for table in (accounts, events, operations):
                self.assertEqual(con.execute(select(func.count()).select_from(table)).scalar_one(), 0)

    def test_cancel_retry_cannot_target_replacement_subscription(self):
        self.activate()
        self.provider.lose_cancel_once = True
        self.assertEqual(self.cancel(at_period_end=False).status_code, 503)
        self.assertEqual(self.client.post(self.base + "/billing/reconcile", headers=self.headers).status_code, 200)
        self.assertEqual(self.checkout(key="d" * 64).status_code, 200)
        self.provider.complete(subscription_id="sub_replacement")
        self.deliver("evt_replacement")
        self.service.process_one()
        self.assertEqual(self.cancel(at_period_end=False).status_code, 409)
        self.assertEqual(self.provider.snapshots["sub_replacement"]["status"], "active")
        self.assertEqual(len(self.provider.cancel_calls), 1)

    def test_concurrent_same_key_checkout_and_cancellation_are_idempotent(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.checkout(), range(2)))
        self.assertEqual([value.status_code for value in responses], [200, 200])
        self.assertEqual(len(self.provider.checkouts), 1)
        self.provider.complete()
        self.deliver()
        self.service.process_one()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.cancel(), range(2)))
        self.assertEqual([value.status_code for value in responses], [200, 200])
        self.assertEqual(len(self.provider.cancel_calls), 1)

    def test_expired_local_timestamp_does_not_replace_completed_checkout(self):
        self.checkout()
        self.provider.complete()
        with self.store.engine.begin() as con:
            row = con.execute(select(operations)).mappings().one()
            con.execute(update(operations).values(result={**row["result"], "expires_at": int(time.time()) - 1}))
        self.assertEqual(self.checkout(key="d" * 64).status_code, 409)
        self.assertEqual(len(self.provider.checkouts), 1)
        self.assertEqual(self.client.post(self.base + "/billing/reconcile", headers=self.headers).status_code, 200)
        self.assertEqual(self.state()["access"], "paid")

    def test_confirmed_expired_checkout_can_be_replaced(self):
        self.checkout()
        list(self.provider.checkouts.values())[-1]["status"] = "expired"
        self.assertEqual(self.client.post(self.base + "/billing/reconcile", headers=self.headers).status_code, 200)
        self.assertEqual(self.checkout(key="d" * 64).status_code, 200)
        self.assertEqual(len(self.provider.checkouts), 2)

    def test_owner_csrf_and_key_boundaries(self):
        self.activate()
        viewer, _, headers = self.other_user("viewer@example.invalid", "viewer")
        outsider, _, outside_headers = self.other_user("other@example.invalid")
        key = self.client.post(self.base + "/keys", json={"name": "CI", "scopes": ["reports:read", "reports:write"]}, headers=self.headers).json()["secret"]
        for method, suffix, body in (("get", "", None), ("post", "/checkout", {"plan": "team"}), ("post", "/cancel", {"at_period_end": False}), ("post", "/reconcile", None)):
            kwargs = {"json": body} if body is not None else {}
            self.assertEqual(getattr(viewer, method)(self.base + "/billing" + suffix, headers=headers, **kwargs).status_code, 403)
            self.assertEqual(getattr(outsider, method)(self.base + "/billing" + suffix, headers=outside_headers, **kwargs).status_code, 404)
            self.assertEqual(getattr(self.client, method)(self.base + "/billing" + suffix, headers={"Authorization": "Bearer " + key}, **kwargs).status_code, 403)
        self.assertEqual(self.client.post(self.base + "/billing/cancel", json={"at_period_end": False}).status_code, 403)
        self.assertEqual(self.client.post(self.base + "/billing/reconcile", headers={**self.headers, "Origin": "https://other.invalid"}).status_code, 403)

    def test_report_seat_and_storage_limits_are_server_enforced(self):
        self.activate()
        self.assertEqual(self.import_report().status_code, 201)
        self.assertEqual(self.import_report().status_code, 200)
        self.assertEqual(self.import_report(key="d" * 64).status_code, 201)
        self.assertEqual(self.import_report(key="e" * 64).status_code, 409)
        self.other_user("one@example.invalid", "viewer")
        self.other_user("two@example.invalid", "viewer")
        self.other_user("three@example.invalid")
        self.assertEqual(self.client.post(self.base + "/members", json={"email": "three@example.invalid", "role": "viewer"}, headers=self.headers).status_code, 409)
        small = create_app(replace(self.settings, billing_storage_limit=1, billing_report_limit=3), billing_provider=self.provider)
        client = TestClient(small, base_url=self.settings.public_origin)
        client.cookies.update(self.client.cookies)
        response = self.import_report(key="f" * 64, client=client)
        self.assertEqual(response.status_code, 409)
        self.assertIn("storage", response.text)
        small.state.store.engine.dispose()

    def test_expired_paid_period_disables_new_writes_without_webhook(self):
        self.activate()
        with self.store.engine.begin() as con:
            con.execute(update(accounts).values(paid_through=utcnow() - timedelta(seconds=1)))
        self.assertEqual(self.state()["access"], "read_only")
        self.assertEqual(self.import_report().status_code, 402)

    def test_old_subscription_event_cannot_overwrite_replacement(self):
        self.activate()
        old = copy.deepcopy(self.provider.snapshots["sub_fixture"])
        self.cancel(at_period_end=False)
        self.assertEqual(self.checkout(key="d" * 64).status_code, 200)
        self.provider.complete(subscription_id="sub_replacement")
        self.deliver("evt_new")
        self.service.process_one()
        self.deliver("evt_old", object_override=old)
        self.service.process_one()
        self.assertEqual(self.state()["access"], "paid")
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(accounts.c.subscription_id)).scalar_one(), "sub_replacement")
            self.assertEqual(con.execute(select(events.c.status).where(events.c.event_id == "evt_old")).scalar_one(), "ignored")

    def test_event_row_lock_prevents_lease_theft_during_reconciliation(self):
        self.checkout()
        self.provider.complete()
        self.deliver()
        entered, release = threading.Event(), threading.Event()
        self.provider.retrieve_hook = lambda: (entered.set(), release.wait(3))
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(self.service.process_one)
            self.assertTrue(entered.wait(2))
            # Make the existing lease eligible for reclamation. SKIP LOCKED
            # still cannot steal it while the first worker commits its state.
            with patch("breakroom_api.team_billing.utcnow", return_value=utcnow() + timedelta(seconds=121)):
                self.assertFalse(self.service.process_one())
            release.set()
            self.assertTrue(running.result(3))
        self.assertEqual(self.state()["access"], "paid")
        with self.store.engine.connect() as con:
            row = con.execute(select(events)).mappings().one()
            self.assertEqual((row["status"], row["attempts"]), ("processed", 1))

    def test_crashed_worker_lease_can_be_reclaimed(self):
        self.checkout()
        self.provider.complete()
        self.deliver()
        with self.store.engine.begin() as con:
            con.execute(update(events).values(status="processing", attempts=1, lease_token="abandoned", lease_until=utcnow() - timedelta(seconds=1)))
        self.assertTrue(self.service.process_one())
        self.assertEqual(self.state()["access"], "paid")

    def test_disabled_mode_keeps_m5_workflow_and_disables_provider_endpoints(self):
        settings = replace(self.settings, billing_mode="disabled", stripe_secret_key=None, stripe_webhook_secret=None, stripe_price_id=None)
        app = create_app(settings)
        client = TestClient(app, base_url=settings.public_origin)
        client.cookies.update(self.client.cookies)
        self.assertEqual(client.get(self.base + "/billing").json()["access"], "local_development")
        self.assertEqual(self.import_report(client=client).status_code, 201)
        self.assertEqual(client.post(self.base + "/billing/checkout", json={"plan": "team"}, headers=self.headers).status_code, 503)
        self.assertEqual(client.post("/api/team/billing/webhook", json={}).status_code, 503)
        app.state.store.engine.dispose()

    def test_fresh_reconciliation_is_a_noop_without_provider_calls(self):
        response = self.client.post(self.base + "/billing/reconcile", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["access"], "read_only")
        self.assertEqual(self.provider.retrieve_calls, [])

    def test_concurrent_imports_cannot_exceed_last_report_slot(self):
        self.activate()
        self.import_report()
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda key: self.import_report(key=key * 64), ("d", "e")))
        self.assertEqual(sorted(value.status_code for value in values), [201, 409])
        self.assertEqual(self.state()["usage"]["reports"], 2)

    def test_retry_exhaustion_is_bounded_and_exposed_in_health(self):
        self.checkout()
        self.provider.complete()
        self.deliver()
        self.provider.unavailable = True
        for _ in range(5):
            self.assertTrue(self.service.process_one())
            with self.store.engine.begin() as con:
                con.execute(update(events).values(retry_at=utcnow() - timedelta(seconds=1)))
        self.assertFalse(self.service.process_one())
        health = self.client.get("/api/team/health").json()["billing"]
        self.assertEqual((health["state"], health["queue"]["failed"]), ("failed", 1))
        self.assertEqual(self.state()["access"], "read_only")
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(events.c.attempts)).scalar_one(), 5)

    def test_project_deletion_requires_provider_confirmed_checkout_expiry(self):
        self.checkout()
        with self.store.engine.begin() as con:
            row = con.execute(select(operations)).mappings().one()
            con.execute(update(operations).values(result={**row["result"], "expires_at": int(time.time()) - 1}))
        self.assertEqual(self.client.delete(self.base, headers=self.headers).status_code, 409)
        list(self.provider.checkouts.values())[-1]["status"] = "expired"
        self.assertEqual(self.client.post(self.base + "/billing/reconcile", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.delete(self.base, headers=self.headers).status_code, 200)

    def test_uncertain_operation_cannot_retry_after_provider_key_horizon(self):
        self.provider.lose_checkout_once = True
        self.assertEqual(self.checkout().status_code, 503)
        with self.store.engine.begin() as con:
            con.execute(update(operations).values(created_at=utcnow() - timedelta(hours=24)))
        self.assertEqual(self.checkout().status_code, 409)
        self.assertEqual(self.checkout(key="d" * 64).status_code, 409)
        self.assertEqual(len(self.provider.checkouts), 1)


if __name__ == "__main__":
    unittest.main()
