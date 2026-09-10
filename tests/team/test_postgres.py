"""Actual PostgreSQL tenant/retention tests; each test owns a disposable schema.

Set BREAKROOM_TEAM_TEST_DATABASE_URL to an isolated test PostgreSQL database.
Never substitutes SQLite or drops objects outside the generated test schema.
"""
import copy
import os
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from breakroom.agents import corrected, faulty
from breakroom.evaluator import run_in_process
from breakroom.scenarios import load_case
from breakroom.uploads import prepare_upload, upload_digest
from breakroom_api.team import create_app
from breakroom_api.team_auth import SESSION_COOKIE
from breakroom_api.team_config import TeamSettings
from breakroom_api.team_db import (api_keys, login_attempts, memberships, projects, reports, sessions,
    suites, token_hash, users, utcnow, versions)


class PostgresTeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upload = prepare_upload(run_in_process(load_case("refund-response-lost"), corrected), redaction_key=b"x" * 32)
        cls.failed = prepare_upload(run_in_process(load_case("refund-response-lost"), faulty), redaction_key=b"x" * 32)

    def setUp(self):
        self.settings = TeamSettings(database_url=os.environ.get("BREAKROOM_TEAM_TEST_DATABASE_URL", TeamSettings.database_url),
            database_schema="test_team_" + uuid.uuid4().hex, environment="test", dev_login=True,
            public_origin="http://127.0.0.1:3000", rate_limit_per_minute=10000)
        self.app = create_app(self.settings)
        self.store = self.app.state.store
        self.client = self.make_client()
        self.client.__enter__()
        self.owner = self.login(self.client, "owner@example.invalid")
        response = self.client.post("/api/team/projects", json={"name": "Private QA", "retention_days": 30}, headers=self.headers(self.client))
        self.assertEqual(response.status_code, 201, response.text)
        self.project = response.json()["id"]
        self.base = "/api/team/projects/" + self.project

    def tearDown(self):
        self.client.__exit__(None, None, None)
        with self.store.engine.begin() as con:
            # Generated locally, regex-validated by TeamSettings; no user selector.
            con.execute(text("DROP SCHEMA " + self.settings.database_schema + " CASCADE"))
        self.store.engine.dispose()

    def make_client(self):
        return TestClient(self.app, base_url=self.settings.public_origin, client=("127.0.0.1", 50000))

    def login(self, client, email):
        response = client.post("/api/team/auth/dev-login", json={"email": email, "display_name": email.split("@")[0]}, headers={"Origin": self.settings.public_origin})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user"]

    def headers(self, client=None):
        return {"Origin": self.settings.public_origin, "X-CSRF-Token": (client or self.client).get("/api/team/me").json()["csrf_token"]}

    def member(self, role, email=None):
        client = self.make_client()
        user = self.login(client, email or role + "@example.invalid")
        result = self.client.post(self.base + "/members", json={"email": user["email"], "role": role}, headers=self.headers())
        self.assertEqual(result.status_code, 201, result.text)
        return client, user

    def import_report(self, client=None, upload=None, key=None, headers=None):
        client, upload = client or self.client, upload or self.upload
        return client.post(self.base + "/reports", json=upload,
            headers={**(headers if headers is not None else self.headers(client)), "Idempotency-Key": key or upload_digest(upload)})

    def suite(self, client=None, headers=None):
        client = client or self.client
        return client.post(self.base + "/suites", json={"name": "Private metadata", "cases": [{"case_id": "private-refund", "case_version": "1.0.0", "manifest_hash": "a" * 64}]}, headers=headers if headers is not None else self.headers(client))

    def key(self, scopes=None):
        response = self.client.post(self.base + "/keys", json={"name": "Fixture key", "scopes": scopes or ["reports:read", "reports:write", "suites:write"]}, headers=self.headers())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_schema_migration_is_real_idempotent_and_constrained(self):
        self.store.migrate()
        with self.store.engine.connect() as con:
            self.assertIn("PostgreSQL", con.execute(text("SELECT version()")).scalar_one())
            self.assertEqual(con.execute(select(versions.c.version).order_by(versions.c.version)).scalars().all(), [1, 2, 3, 4])
            self.assertEqual(con.execute(text("SELECT current_schema()")).scalar_one(), self.settings.database_schema)
        with self.assertRaises(IntegrityError), self.store.engine.begin() as con:
            con.execute(update(projects).where(projects.c.id == self.project).values(retention_days=0))
        with self.assertRaises(IntegrityError), self.store.engine.begin() as con:
            con.execute(update(memberships).values(role="admin"))

    def test_session_hash_csrf_and_logout(self):
        secret = self.client.cookies.get(SESSION_COOKIE)
        with self.store.engine.connect() as con:
            row = con.execute(select(sessions)).mappings().one()
            self.assertEqual(row["token_hash"], token_hash(secret))
            self.assertNotIn(secret, str(dict(row)))
        for headers in ({}, {"Origin": "https://attacker.invalid", "X-CSRF-Token": self.headers()["X-CSRF-Token"]}):
            self.assertEqual(self.client.post("/api/team/projects", json={"name": "Bad"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/team/auth/logout", headers=self.headers()).status_code, 200)
        self.assertIsNone(self.client.get("/api/team/me").json()["user"])
        self.assertEqual(self.client.get(self.base).status_code, 401)

    def test_session_expiry_is_checked_on_every_request(self):
        with self.store.engine.begin() as con:
            con.execute(update(sessions).values(expires_at=utcnow() - timedelta(seconds=1)))
        self.assertEqual(self.client.get(self.base).status_code, 401)
        self.assertIsNone(self.client.get("/api/team/me").json()["user"])
        self.assertIsNone(self.make_client().get("/api/team/me").json()["user"])
        self.assertEqual(self.make_client().get("/api/team/me", headers={"Authorization": "Bearer invalid"}).status_code, 401)

    def test_disabling_development_login_invalidates_existing_dev_credentials(self):
        key = self.key()
        cookie = self.client.cookies.get(SESSION_COOKIE)
        settings = TeamSettings(database_url=self.settings.database_url, database_schema=self.settings.database_schema,
            environment="production", public_origin="https://team.example.invalid", dev_login=False, password_enabled=False)
        app = create_app(settings)
        client = TestClient(app, base_url=settings.public_origin)
        self.assertIsNone(client.get("/api/team/me", headers={"Cookie": SESSION_COOKIE + "=" + cookie}).json()["user"])
        self.assertEqual(client.get(self.base, headers={"Cookie": SESSION_COOKIE + "=" + cookie}).status_code, 401)
        self.assertEqual(client.get(self.base, headers={"Authorization": "Bearer " + key["secret"]}).status_code, 401)
        app.state.store.engine.dispose()

    def test_atomic_oidc_attempt_consumption_and_timestamp_boundary(self):
        self.store.create_login_attempt("a" * 64, "nonce", "verifier", time.time() + 60)
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: self.store.consume_login_attempt("a" * 64), range(2)))
        self.assertEqual(sum(value is not None for value in values), 1)
        self.assertGreater(next(value for value in values if value)["expires_at"], time.time())

    def test_bounded_expiry_sweep_deletes_reports_and_auth_rows(self):
        self.import_report()
        self.import_report(upload=self.failed)
        with self.store.engine.begin() as con:
            con.execute(update(reports).values(expires_at=utcnow() - timedelta(seconds=1)))
        for index in range(2):
            self.store.create_session(self.owner["id"], str(index) * 64, "csrf", time.time() - 1)
            # Insert directly because the login creation path already sweeps old attempts.
            with self.store.engine.begin() as con:
                con.execute(insert(login_attempts).values(state_hash=str(index) * 64, nonce="nonce", code_verifier="verifier", expires_at=utcnow() - timedelta(seconds=1)))
        expected = {"reports": 1, "sessions": 1, "login_attempts": 1, "invitations": 0, "account_tokens": 0, "auth_limits": 0}
        self.assertEqual(self.store.cleanup_expired(batch_size=1), expected)
        self.assertEqual(self.store.cleanup_expired(), expected)
        self.assertIsNotNone(self.client.get("/api/team/me").json()["user"])

    def test_startup_and_periodic_sweep_delete_expired_records(self):
        self.import_report()
        with self.store.engine.begin() as con:
            con.execute(update(reports).values(expires_at=utcnow() - timedelta(seconds=1)))
        settings = TeamSettings(database_url=self.settings.database_url, database_schema=self.settings.database_schema, environment="test", retention_sweep_seconds=0.1)
        service = create_app(settings)
        with TestClient(service) as client:
            with self.store.engine.connect() as con:
                self.assertEqual(con.execute(select(func.count()).select_from(reports)).scalar_one(), 0)
            self.store.create_session(self.owner["id"], "z" * 64, "csrf", time.time() - 1)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self.store.engine.connect() as con:
                    count = con.execute(select(func.count()).select_from(sessions).where(sessions.c.token_hash == "z" * 64)).scalar_one()
                if not count:
                    break
                time.sleep(0.02)
            self.assertEqual(count, 0)
            self.assertEqual(client.get("/api/team/health").json()["retention"], "ready")

    def test_import_is_immutable_idempotent_and_customer_labelled(self):
        first = self.import_report(key="b" * 64)
        self.assertEqual(first.status_code, 201, first.text)
        row = first.json()
        self.assertEqual(row["provenance"], "customer_generated")
        second = self.import_report(key="b" * 64)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(row["id"], second.json()["id"])
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(self.import_report(upload=self.failed, key="b" * 64).status_code, 409)
        for values in ({"upload": self.failed}, {"verdict": "FAIL"}, {"project_id": str(uuid.uuid4())}):
            with self.assertRaises(DBAPIError), self.store.engine.begin() as con:
                con.execute(update(reports).where(reports.c.id == row["id"]).values(**values))
        saved = self.client.get(self.base + "/reports/" + row["id"]).json()
        self.assertEqual(saved["upload"], self.upload)
        exported = self.client.get(self.base + "/reports/" + row["id"] + "/export")
        self.assertEqual(exported.json(), self.upload)
        self.assertIn("attachment", exported.headers["content-disposition"])

    def test_viewer_reads_but_developer_uploads_and_suites(self):
        viewer, _ = self.member("viewer")
        developer, _ = self.member("developer")
        self.assertEqual(self.import_report(viewer).status_code, 403)
        self.assertEqual(self.suite(viewer).status_code, 403)
        result = self.import_report(developer)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(self.suite(developer).status_code, 201)
        self.assertEqual(viewer.get(self.base + "/reports/" + result.json()["id"] + "/evidence").status_code, 200)
        for client in (viewer, developer):
            self.assertEqual(client.get(self.base + "/keys").status_code, 403)
            self.assertEqual(client.delete(self.base, headers=self.headers(client)).status_code, 403)
            self.assertEqual(client.patch(self.base, json={"retention_days": 1}, headers=self.headers(client)).status_code, 403)

    def test_cross_tenant_nested_paths_never_leak(self):
        rid = self.import_report().json()["id"]
        sid = self.suite().json()["id"]
        key = self.key()
        outsider = self.make_client()
        self.login(outsider, "outside@example.invalid")
        other = outsider.post("/api/team/projects", json={"name": "Other"}, headers=self.headers(outsider)).json()["id"]
        paths = ["", "/members", "/keys", "/suites", "/reports", "/reports/" + rid, "/reports/" + rid + "/evidence", "/reports/" + rid + "/export"]
        for path in paths:
            self.assertEqual(outsider.get(self.base + path).status_code, 404, path)
        for suffix in ("", "/evidence", "/export"):
            self.assertEqual(outsider.get("/api/team/projects/" + other + "/reports/" + rid + suffix).status_code, 404)
        self.assertEqual(outsider.post("/api/team/projects/" + other + "/compare", json={"baseline_id": rid, "candidate_id": rid}, headers=self.headers(outsider)).status_code, 404)
        for path in ("/suites/" + sid, "/keys/" + key["id"]):
            self.assertEqual(outsider.delete("/api/team/projects/" + other + path, headers=self.headers(outsider)).status_code, 404)
        self.assertEqual(outsider.delete(self.base, headers=self.headers(outsider)).status_code, 404)

    def test_key_secret_once_scopes_and_revocation(self):
        key = self.key(["reports:write"])
        headers = {"Authorization": "Bearer " + key["secret"]}
        with self.store.engine.connect() as con:
            row = dict(con.execute(select(api_keys)).mappings().one())
        self.assertEqual(row["token_hash"], token_hash(key["secret"]))
        self.assertNotIn(key["secret"], str(row))
        listing = self.client.get(self.base + "/keys").json()
        self.assertNotIn("secret", str(listing))
        self.assertNotIn("token_hash", str(listing))
        self.assertEqual(self.import_report(headers=headers).status_code, 201)
        self.assertEqual(self.client.get(self.base + "/reports", headers=headers).status_code, 403)
        self.assertEqual(self.client.get(self.base + "/members", headers=headers).status_code, 403)
        self.assertEqual(self.client.get(self.base + "/suites", headers=headers).status_code, 403)
        self.assertEqual(self.suite(headers=headers).status_code, 403)
        self.assertEqual(self.client.delete(self.base, headers=headers).status_code, 403)
        self.assertEqual(self.client.delete(self.base + "/keys/" + key["id"], headers=self.headers()).status_code, 200)
        self.assertEqual(self.import_report(headers=headers).status_code, 401)

    def test_keys_obey_creator_current_role_and_membership(self):
        key = self.key()
        second, _ = self.member("owner", "second@example.invalid")
        change = self.base + "/members/" + self.owner["id"]
        self.assertEqual(second.patch(change, json={"role": "viewer"}, headers=self.headers(second)).status_code, 200)
        auth = {"Authorization": "Bearer " + key["secret"]}
        self.assertEqual(self.import_report(headers=auth).status_code, 403)
        self.assertEqual(self.client.get(self.base + "/reports", headers=auth).status_code, 200)
        self.assertEqual(second.delete(change, headers=self.headers(second)).status_code, 200)
        self.assertEqual(self.client.get(self.base + "/reports", headers=auth).status_code, 401)
        with self.store.engine.connect() as con:
            self.assertIsNotNone(con.execute(select(api_keys.c.revoked_at)).scalar_one())

    def test_key_is_project_scoped_and_expires(self):
        key = self.key()
        other = self.client.post("/api/team/projects", json={"name": "Second"}, headers=self.headers()).json()["id"]
        auth = {"Authorization": "Bearer " + key["secret"]}
        self.assertEqual(self.client.get("/api/team/projects/" + other, headers=auth).status_code, 403)
        with self.store.engine.begin() as con:
            con.execute(update(api_keys).values(expires_at=utcnow() - timedelta(seconds=1)))
        self.assertEqual(self.client.get(self.base + "/reports", headers=auth).status_code, 401)

    def test_last_owner_protection_and_unknown_email(self):
        path = self.base + "/members/" + self.owner["id"]
        self.assertEqual(self.client.delete(path, headers=self.headers()).status_code, 409)
        self.assertEqual(self.client.patch(path, json={"role": "viewer"}, headers=self.headers()).status_code, 409)
        response = self.client.post(self.base + "/members", json={"email": "missing@example.invalid", "role": "viewer"}, headers=self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertIn("no invitation was sent", response.text)

    def test_concurrent_owner_demotions_leave_one_owner(self):
        second, second_user = self.member("owner", "second@example.invalid")
        requests = [(self.client, self.owner["id"], self.headers()), (second, second_user["id"], self.headers(second))]
        def demote(item):
            client, uid, headers = item
            return client.patch(self.base + "/members/" + uid, json={"role": "viewer"}, headers=headers).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(demote, requests)), [200, 409])
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(func.count()).select_from(memberships).where(memberships.c.role == "owner")).scalar_one(), 1)

    def test_concurrent_imports_share_one_immutable_row(self):
        headers = self.headers()
        def submit(_):
            return self.import_report(headers=headers, key="c" * 64)
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(response.status_code for response in values), [200, 201])
        self.assertEqual(values[0].json()["id"], values[1].json()["id"])

    def test_retention_enforced_on_every_nested_read_and_cleanup(self):
        rid = self.import_report().json()["id"]
        before = self.client.get(self.base + "/reports/" + rid).json()["expires_at"]
        self.assertEqual(self.client.patch(self.base, json={"retention_days": 1}, headers=self.headers()).status_code, 200)
        after = self.client.get(self.base + "/reports/" + rid).json()["expires_at"]
        self.assertLess(after, before)
        self.client.patch(self.base, json={"retention_days": 365}, headers=self.headers())
        self.assertEqual(self.client.get(self.base + "/reports/" + rid).json()["expires_at"], after)
        with self.store.engine.begin() as con:
            con.execute(update(reports).values(expires_at=utcnow() - timedelta(seconds=1)))
        for suffix in ("", "/evidence", "/export"):
            self.assertEqual(self.client.get(self.base + "/reports/" + rid + suffix).status_code, 404)
        self.assertEqual(self.client.get(self.base + "/reports").json()["items"], [])
        self.assertEqual(self.import_report().status_code, 410)
        self.assertEqual(self.client.post(self.base + "/compare", json={"baseline_id": rid, "candidate_id": rid}, headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.post(self.base + "/retention/cleanup", headers=self.headers()).json(), {"deleted_reports": 1})

    def test_delete_project_cascades_all_owned_records(self):
        rid = self.import_report().json()["id"]
        self.suite()
        self.key()
        self.member("viewer")
        self.assertEqual(self.client.delete(self.base, headers=self.headers()).status_code, 200)
        with self.store.engine.connect() as con:
            for table in (projects, memberships, reports, api_keys, suites):
                self.assertEqual(con.execute(select(func.count()).select_from(table)).scalar_one(), 0, table.name)
            self.assertEqual(con.execute(select(func.count()).select_from(users)).scalar_one(), 2)
        self.assertEqual(self.client.get(self.base + "/reports/" + rid + "/export").status_code, 404)

    def test_private_metadata_rejects_executable_or_unbounded_fields(self):
        valid = {"name": "Private", "cases": [{"case_id": "case", "case_version": "1.0.0", "manifest_hash": "a" * 64}]}
        for extra in ("source", "command", "url", "path"):
            body = copy.deepcopy(valid)
            body["cases"][0][extra] = "https://attacker.invalid"
            self.assertEqual(self.client.post(self.base + "/suites", json=body, headers=self.headers()).status_code, 422)
        for count in (0, 129):
            body = valid | {"cases": valid["cases"] * count}
            self.assertEqual(self.client.post(self.base + "/suites", json=body, headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.post(self.base + "/suites", json=valid | {"cases": valid["cases"] * 2}, headers=self.headers()).status_code, 422)

    def test_report_upload_requires_verified_minimized_envelope(self):
        for upload in ({"url": "https://attacker.invalid/report.json"}, self.upload["report"], self.upload | {"command": "shell"}):
            self.assertEqual(self.import_report(upload=upload, key="e" * 64).status_code, 422)
        poisoned = copy.deepcopy(self.upload)
        poisoned["report"]["agent_result"]["summary"] = "Customer PII that was never minimized"
        self.assertEqual(self.import_report(upload=poisoned, key="e" * 64).status_code, 422)
        response = self.client.post(self.base + "/reports", content='{"schema_version":"1.0","schema_version":"1.0"}', headers={**self.headers(), "Idempotency-Key": "d" * 64, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)

    def test_reports_compare_uses_compatibility_checks(self):
        one = self.import_report().json()["id"]
        two = self.import_report(upload=self.failed).json()["id"]
        response = self.client.post(self.base + "/compare", json={"baseline_id": two, "candidate_id": one}, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provenance"], "customer_generated")

    def test_request_size_rate_and_no_store(self):
        headers = self.headers()
        self.assertEqual(self.client.post(self.base + "/reports", content=b"x" * (2 * 1024 * 1024 + 1), headers=headers).status_code, 413)
        self.assertEqual(self.client.post(self.base + "/reports", content=iter([b"x" * 1048576] * 3), headers=headers).status_code, 413)
        self.assertEqual(self.client.get("/api/team/me").headers["cache-control"], "no-store")
        # Same actual ASGI middleware, smaller independent app budget.
        limited = create_app(TeamSettings(database_schema=self.settings.database_schema, environment="test", rate_limit_per_minute=2), self.store)
        client = TestClient(limited)
        self.assertEqual([client.get("/api/team/me").status_code for _ in range(3)], [200, 200, 429])

    def test_identity_never_links_accounts_by_unverified_email(self):
        with self.assertRaisesRegex(ValueError, "different verified identity"):
            self.store.create_user("https://other.invalid", "new", self.owner["email"], "Other identity")
        with self.assertRaisesRegex(ValueError, "email changed"):
            self.store.create_user("breakroom:development", self.owner["email"], "changed@example.invalid", "Changed")


if __name__ == "__main__":
    unittest.main()
