"""Real PostgreSQL account/security lifecycle; all mail is captured offline."""
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update

from breakroom_api.password_auth import HASHER, SMTPMailer
from breakroom_api.password_db import (account_tokens, auth_limits, consume_attempts,
    credentials)
from breakroom_api.team import create_app
from breakroom_api.team_auth import SESSION_COOKIE
from breakroom_api.team_config import TeamSettings
from breakroom_api.team_db import sessions, token_hash, users, utcnow

PASSWORD = " correct horse 2026 "
NEW_PASSWORD = " replaced battery 2026 "


class CaptureMailer:
    def __init__(self):
        self.messages = []

    def send(self, recipient, purpose, link):
        self.messages.append((recipient, purpose, link))

    def token(self, purpose):
        link = [link for _, kind, link in self.messages if kind == purpose][-1]
        assert not urlsplit(link).query
        return parse_qs(urlsplit(link).fragment)["token"][0]


class PasswordAccountTests(unittest.TestCase):
    def setUp(self):
        self.settings = TeamSettings(database_url=os.environ.get("BREAKROOM_TEAM_TEST_DATABASE_URL", TeamSettings.database_url),
            database_schema="test_password_" + uuid.uuid4().hex, environment="test", dev_login=False,
            public_origin="http://127.0.0.1:3000", rate_limit_per_minute=10000)
        self.app = create_app(self.settings)
        self.store = self.app.state.store
        self.mailer = CaptureMailer()
        self.app.state.password_mailer = self.mailer
        self.client = self.make_client()
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        with self.store.engine.begin() as con:
            con.execute(text("DROP SCHEMA " + self.settings.database_schema + " CASCADE"))
        self.store.engine.dispose()

    def make_client(self, app=None, origin=None):
        return TestClient(app or self.app, base_url=origin or self.settings.public_origin, client=("127.0.0.1", 50000))

    def headers(self, csrf=False):
        values = {"Origin": self.settings.public_origin}
        if csrf:
            values["X-CSRF-Token"] = self.client.get("/api/team/me").json()["csrf_token"]
        return values

    def register(self, email="person@example.invalid", password=PASSWORD):
        response = self.client.post("/api/team/auth/register", json={"email": email, "display_name": "Actual person", "password": password}, headers=self.headers())
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def login(self, *, email="person@example.invalid", password=PASSWORD, client=None):
        return (client or self.client).post("/api/team/auth/password-login", json={"email": email, "password": password}, headers=self.headers())

    def reset_request(self, email="person@example.invalid"):
        return self.client.post("/api/team/auth/request-password-reset", json={"email": email}, headers=self.headers())

    def test_register_stores_only_argon2id_digest_and_opaque_session_hash(self):
        response = self.register(email=" Person@EXAMPLE.invalid ")
        value = response.json()
        self.assertEqual(value["user"]["email"], "person@example.invalid")
        self.assertFalse(value["user"]["email_verified"])
        self.assertEqual(value["user"]["auth_method"], "password")
        self.assertTrue(value["verification_sent"])
        self.assertNotIn(PASSWORD, response.text)
        self.assertNotIn(self.mailer.token("verification"), response.text)
        with self.store.engine.connect() as con:
            credential = con.execute(select(credentials)).mappings().one()
            session = con.execute(select(sessions)).mappings().one()
            self.assertTrue(credential["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=4$"))
            self.assertTrue(HASHER.verify(credential["password_hash"], PASSWORD))
            self.assertIsNone(credential["verified_at"])
            self.assertEqual(session["token_hash"], token_hash(self.client.cookies.get(SESSION_COOKIE)))
            self.assertNotIn(PASSWORD, str(dict(credential)))
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        self.assertIn("no-store", response.headers["cache-control"])

    def test_durable_login_after_app_restart_and_password_whitespace_is_significant(self):
        self.register()
        restarted = create_app(self.settings)
        with self.make_client(restarted) as client:
            self.assertEqual(self.login(client=client).status_code, 200)
            self.assertEqual(self.login(client=client, password=PASSWORD.strip()).status_code, 401)
            self.assertEqual(client.get("/api/team/me").json()["user"]["email"], "person@example.invalid")

    def test_login_failures_are_generic_and_missing_account_uses_dummy_hash(self):
        self.register()
        wrong = self.login(password=NEW_PASSWORD)
        with patch("breakroom_api.password_auth.HASHER", wraps=HASHER) as hasher:
            absent = self.login(email="absent@example.invalid")
            self.assertEqual(hasher.verify.call_count, 1)
            self.assertTrue(hasher.verify.call_args.args[0].startswith("$argon2id$"))
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(absent.json(), wrong.json())

    def test_unknown_fields_bounds_and_bad_addresses_do_not_leak_rejected_input(self):
        valid = {"email": "person@example.invalid", "display_name": "Actual person", "password": PASSWORD}
        for mutation in ({"email": "bad\n@example.invalid"}, {"password": "x" * 129}, {"password": "secret123"},
                         {"email": "a@-bad.invalid"}, {"admin": True}, {"display_name": "   "}):
            response = self.client.post("/api/team/auth/register", json={**valid, **mutation}, headers=self.headers())
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(PASSWORD, response.text)
            self.assertNotIn("secret123", response.text)

    def test_exact_origin_is_required_for_public_account_posts(self):
        for origin in (None, "https://evil.invalid", "http://127.0.0.1:3000.evil.invalid", "http://127.0.0.1:3000/"):
            response = self.client.post("/api/team/auth/register", json={"email": "person@example.invalid", "display_name": "P", "password": PASSWORD}, headers={"Origin": origin} if origin else {})
            self.assertEqual(response.status_code, 403)
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(func.count()).select_from(users)).scalar_one(), 0)

    def test_registration_does_not_link_or_replace_existing_identity(self):
        self.register()
        response = self.client.post("/api/team/auth/register", json={"email": "PERSON@example.invalid", "display_name": "Attacker", "password": NEW_PASSWORD}, headers=self.headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.login().status_code, 200)
        self.store.create_user("https://id.example.invalid", "other", "sso@example.invalid", "SSO")
        response = self.client.post("/api/team/auth/register", json={"email": "sso@example.invalid", "display_name": "Other", "password": PASSWORD}, headers=self.headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.login(email="sso@example.invalid").status_code, 401)

    def test_verification_is_single_use_hashed_and_visible_on_existing_session(self):
        self.register()
        raw = self.mailer.token("verification")
        with self.store.engine.connect() as con:
            row = con.execute(select(account_tokens)).mappings().one()
            self.assertEqual(row["token_hash"], token_hash(raw))
            self.assertNotIn(raw, str(dict(row)))
        response = self.client.post("/api/team/auth/verify-email", json={"token": raw}, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/team/me").json()["user"]["email_verified"])
        self.assertEqual(self.client.post("/api/team/auth/verify-email", json={"token": raw}, headers=self.headers()).status_code, 400)

    def test_verification_resend_requires_csrf_and_supersedes_previous_link(self):
        self.register()
        old = self.mailer.token("verification")
        self.assertEqual(self.client.post("/api/team/auth/request-verification", json={}, headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.post("/api/team/auth/request-verification", json={}, headers=self.headers(True)).status_code, 202)
        new = self.mailer.token("verification")
        self.assertNotEqual(old, new)
        self.assertEqual(self.client.post("/api/team/auth/verify-email", json={"token": old}, headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.post("/api/team/auth/verify-email", json={"token": new}, headers=self.headers()).status_code, 200)

    def test_expired_and_wrong_purpose_tokens_cannot_verify_or_reset(self):
        self.register()
        raw = self.mailer.token("verification")
        self.assertEqual(self.client.post("/api/team/auth/reset-password", json={"token": raw, "password": NEW_PASSWORD}, headers=self.headers()).status_code, 400)
        with self.store.engine.begin() as con:
            con.execute(update(account_tokens).values(expires_at=utcnow() - timedelta(seconds=1)))
        self.assertEqual(self.client.post("/api/team/auth/verify-email", json={"token": raw}, headers=self.headers()).status_code, 400)
        self.assertEqual(self.login().status_code, 200)

    def test_concurrent_verification_consumes_token_exactly_once(self):
        self.register()
        raw = self.mailer.token("verification")
        def accept(_):
            return self.make_client().post("/api/team/auth/verify-email", json={"token": raw}, headers=self.headers()).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(accept, range(4)))
        self.assertEqual(sorted(results), [200, 400, 400, 400])

    def test_reset_is_generic_single_use_and_revokes_every_session(self):
        self.register()
        second = self.make_client()
        self.assertEqual(self.login(client=second).status_code, 200)
        old_session = self.client.cookies.get(SESSION_COOKIE)
        old_csrf = self.client.get("/api/team/me").json()["csrf_token"]
        existing = self.reset_request()
        absent = self.reset_request("missing@example.invalid")
        self.assertEqual(existing.status_code, 202)
        self.assertEqual(existing.json(), absent.json())
        raw = self.mailer.token("reset")
        response = self.client.post("/api/team/auth/reset-password", json={"token": raw, "password": NEW_PASSWORD}, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(second.get("/api/team/me").json()["user"])
        self.assertEqual(self.client.post("/api/team/auth/reset-password", json={"token": raw, "password": PASSWORD}, headers=self.headers()).status_code, 400)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code, 200)
        replay = self.make_client()
        replay.cookies.set(SESSION_COOKIE, old_session, path="/api/team")
        self.assertEqual(replay.post("/api/team/projects", json={"name": "Stale"}, headers={**self.headers(), "X-CSRF-Token": old_csrf}).status_code, 401)

    def test_reset_revokes_other_outstanding_account_links(self):
        self.register()
        verification = self.mailer.token("verification")
        self.reset_request()
        raw = self.mailer.token("reset")
        self.client.post("/api/team/auth/reset-password", json={"token": raw, "password": NEW_PASSWORD}, headers=self.headers())
        self.assertEqual(self.client.post("/api/team/auth/verify-email", json={"token": verification}, headers=self.headers()).status_code, 400)

    def test_password_reset_serializes_with_inflight_login_and_revokes_its_session(self):
        self.register()
        self.reset_request()
        raw = self.mailer.token("reset")
        entered, release = threading.Event(), threading.Event()
        import breakroom_api.password_auth as auth
        original = auth.verify_password
        def paused_verify(encoded, password):
            valid = original(encoded, password)
            entered.set()
            self.assertTrue(release.wait(5))
            return valid
        other = self.make_client()
        with patch("breakroom_api.password_auth.verify_password", side_effect=paused_verify), ThreadPoolExecutor(max_workers=2) as pool:
            login = pool.submit(self.login, client=other)
            self.assertTrue(entered.wait(5))
            reset = pool.submit(self.client.post, "/api/team/auth/reset-password",
                json={"token": raw, "password": NEW_PASSWORD}, headers=self.headers())
            release.set()
            self.assertEqual(login.result(timeout=5).status_code, 200)
            self.assertEqual(reset.result(timeout=5).status_code, 200)
        self.assertIsNone(other.get("/api/team/me").json()["user"])
        self.assertEqual(self.login().status_code, 401)

    def test_change_password_requires_current_secret_origin_csrf_and_rotates_session(self):
        self.register()
        previous = self.client.cookies.get(SESSION_COOKIE)
        second = self.make_client()
        self.login(client=second)
        body = {"current_password": PASSWORD, "password": NEW_PASSWORD}
        self.assertEqual(self.client.post("/api/team/auth/change-password", json=body, headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.post("/api/team/auth/change-password", json={**body, "current_password": NEW_PASSWORD}, headers=self.headers(True)).status_code, 401)
        response = self.client.post("/api/team/auth/change-password", json=body, headers=self.headers(True))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(previous, self.client.cookies.get(SESSION_COOKIE))
        self.assertIsNone(second.get("/api/team/me").json()["user"])
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code, 200)

    def test_attempt_limits_survive_app_restart_and_ignore_forwarded_ip(self):
        for _ in range(8):
            self.assertEqual(self.login(email="absent@example.invalid").status_code, 401)
        restarted = create_app(self.settings)
        with self.make_client(restarted) as client:
            response = client.post("/api/team/auth/password-login", json={"email": "absent@example.invalid", "password": PASSWORD}, headers={**self.headers(), "X-Forwarded-For": "198.51.100.1"})
            self.assertEqual(response.status_code, 429)
            self.assertIn("retry-after", response.headers)
        with self.store.engine.connect() as con:
            rows = con.execute(select(auth_limits)).mappings().all()
            self.assertNotIn("absent@example.invalid", str(rows))
            self.assertNotIn("127.0.0.1", str(rows))

    def test_concurrent_attempt_reservations_cannot_exceed_account_budget(self):
        def reserve(index):
            return consume_attempts(self.store, "login", email="person@example.invalid", peer="peer-" + str(index))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(16)))
        self.assertEqual(results.count(True), 8)

    def test_expired_attempt_budget_resets_and_cleanup_removes_expired_tokens(self):
        self.register()
        with self.store.engine.begin() as con:
            con.execute(update(auth_limits).values(expires_at=utcnow() - timedelta(seconds=1)))
            con.execute(update(account_tokens).values(expires_at=utcnow() - timedelta(seconds=1)))
        self.assertTrue(consume_attempts(self.store, "register", email="person@example.invalid", peer="127.0.0.1"))
        self.store.cleanup_expired()
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(func.count()).select_from(account_tokens)).scalar_one(), 0)

    def test_local_without_smtp_is_truthfully_unverified_but_usable(self):
        self.app.state.password_mailer = None
        response = self.register()
        self.assertFalse(response.json()["verification_sent"])
        self.assertFalse(response.json()["mail_available"])
        self.assertFalse(response.json()["user"]["email_verified"])
        self.assertEqual(self.client.post("/api/team/projects", json={"name": "My workspace"}, headers=self.headers(True)).status_code, 201)
        self.assertFalse(self.reset_request().json()["mail_available"])

    def test_mail_failure_does_not_rollback_account_or_expose_tokens(self):
        self.app.state.password_mailer.send = MagicMock(side_effect=RuntimeError("sensitive SMTP credential"))
        response = self.register()
        self.assertFalse(response.json()["verification_sent"])
        self.assertNotIn("sensitive", response.text)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.app.state.password_mail_status, "retry_required")

    def test_mail_health_is_aggregate_and_success_clears_failure(self):
        self.app.state.password_mailer.send = MagicMock(side_effect=RuntimeError("sensitive SMTP credential person@example.invalid"))
        self.register()
        response = self.client.get("/api/team/health")
        self.assertEqual(response.json()["mail"], {"configured": True, "status": "retry_required"})
        self.assertNotIn("sensitive", response.text)
        self.assertNotIn("person@example.invalid", response.text)
        self.app.state.password_mailer = self.mailer = CaptureMailer()
        response = self.client.post("/api/team/auth/request-verification", json={}, headers=self.headers(True))
        self.assertTrue(response.json()["verification_sent"])
        health = self.client.get("/api/team/health")
        self.assertEqual(health.json()["mail"], {"configured": True, "status": "ready"})
        self.assertNotIn(self.mailer.token("verification"), health.text)

    def test_disabled_password_routes_cannot_register_or_login(self):
        self.register()
        token = self.client.cookies.get(SESSION_COOKIE)
        app = create_app(replace(self.settings, password_enabled=False))
        with self.make_client(app) as client:
            self.assertFalse(client.get("/api/team/auth/config").json()["password_available"])
            self.assertEqual(self.login(client=client).status_code, 404)
            client.cookies.set(SESSION_COOKIE, token, path="/api/team")
            self.assertIsNone(client.get("/api/team/me").json()["user"])

    def test_production_requires_verified_account_before_workspace_access(self):
        config = replace(self.settings, environment="production", public_origin="https://app.example.invalid", smtp_host="smtp.example.invalid", smtp_from="accounts@example.invalid")
        app = create_app(config)
        app.state.password_mailer = self.mailer
        with self.make_client(app, config.public_origin) as client:
            headers = {"Origin": config.public_origin}
            response = client.post("/api/team/auth/register", json={"email": "person@example.invalid", "display_name": "Person", "password": PASSWORD}, headers=headers)
            self.assertEqual(response.status_code, 201)
            self.assertIn("Secure", response.headers["set-cookie"])
            self.assertFalse(client.get("/api/team/me").json()["user"]["email_verified"])
            headers["X-CSRF-Token"] = response.json()["csrf_token"]
            self.assertEqual(client.post("/api/team/projects", json={"name": "Blocked"}, headers=headers).status_code, 403)
            self.assertEqual(client.post("/api/team/auth/verify-email", json={"token": self.mailer.token("verification")}, headers=headers).status_code, 200)
            self.assertEqual(client.post("/api/team/projects", json={"name": "Verified workspace"}, headers=headers).status_code, 201)


class MailConfigurationTests(unittest.TestCase):
    def test_production_fails_closed_without_smtp(self):
        with self.assertRaisesRegex(ValueError, "SMTP"):
            TeamSettings(environment="production", public_origin="https://app.example.invalid").validate()
        TeamSettings(environment="production", public_origin="https://app.example.invalid", password_enabled=False).validate()

    def test_smtp_rejects_plaintext_partial_credentials_and_header_injection(self):
        for changes in ({"smtp_mode": "none"}, {"smtp_host": "smtp.example.invalid"}, {"smtp_username": "account"},
                        {"smtp_host": "smtp.example.invalid", "smtp_from": "hello@example.invalid\nBcc: evil@example.invalid"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                TeamSettings(**changes).validate()

    def test_smtp_ssl_and_starttls_validate_tls_before_credentials(self):
        settings = TeamSettings(smtp_host="smtp.example.invalid", smtp_from="accounts@example.invalid", smtp_username="fixture", smtp_password="offline-fixture-secret")
        for mode in ("ssl", "starttls"):
            config = replace(settings, smtp_mode=mode, smtp_port=465 if mode == "ssl" else 587)
            with patch("breakroom_api.password_auth.smtplib.SMTP_SSL") as ssl_smtp, patch("breakroom_api.password_auth.smtplib.SMTP") as start_smtp:
                SMTPMailer(config).send("person@example.invalid", "verification", "https://app.example.invalid/verify-email#token=fixture")
                factory = ssl_smtp if mode == "ssl" else start_smtp
                smtp = factory.return_value.__enter__.return_value
                if mode == "ssl":
                    self.assertEqual(factory.call_args.kwargs["context"].verify_mode, 2)
                    start_smtp.assert_not_called()
                else:
                    self.assertEqual(smtp.starttls.call_args.kwargs["context"].verify_mode, 2)
                    names = [entry[0] for entry in smtp.mock_calls]
                    self.assertLess(names.index("starttls"), names.index("login"))
                    ssl_smtp.assert_not_called()
                message = smtp.send_message.call_args.args[0]
                self.assertNotIn(config.smtp_password, message.as_string())
                self.assertEqual(message["To"], "person@example.invalid")


if __name__ == "__main__":
    unittest.main()
