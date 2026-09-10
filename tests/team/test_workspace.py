"""Real PostgreSQL workspace boundaries, readiness and invitation consumption."""
import copy
import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit, parse_qs

from sqlalchemy import func, insert, select, update
from breakroom.agents import corrected
from breakroom.evaluator import run_in_process
from breakroom.scenarios import content_hash, load_case
from breakroom.uploads import prepare_upload
from breakroom_api.team_db import invitations, memberships, reports, token_hash, utcnow
from breakroom_api.team_auth import SESSION_COOKIE
from breakroom_api.password_db import credentials
from breakroom_api.team_workspace import suite_readiness
import test_postgres


class WorkspaceTests(unittest.TestCase):
    # Share only real-DB fixture helpers, not the previous 23 test methods.
    setUpClass = classmethod(test_postgres.PostgresTeamTests.setUpClass.__func__)
    setUp = test_postgres.PostgresTeamTests.setUp
    tearDown = test_postgres.PostgresTeamTests.tearDown
    make_client = test_postgres.PostgresTeamTests.make_client
    login = test_postgres.PostgresTeamTests.login
    headers = test_postgres.PostgresTeamTests.headers
    member = test_postgres.PostgresTeamTests.member
    import_report = test_postgres.PostgresTeamTests.import_report
    key = test_postgres.PostgresTeamTests.key

    def invite(self, email="recipient@example.invalid", role="viewer"):
        result = self.client.post(self.base + "/invitations", json={"email": email, "role": role}, headers=self.headers())
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def token(self, invite):
        parts = urlsplit(invite["invite_url"])
        self.assertEqual(parts.query, "")
        return parse_qs(parts.fragment)["token"][0]

    def accept(self, client, token, headers=None):
        return client.post("/api/team/invitations/accept", json={"token": token}, headers=headers if headers is not None else self.headers(client))

    def create_suite(self, case=None):
        case = case or next(case for case in self.client.get("/api/team/catalog").json()["items"] if case["case_id"] == "refund-response-lost")
        metadata = {key: case[key] for key in ("case_id", "case_version", "manifest_hash")}
        result = self.client.post(self.base + "/suites", json={"name": "Release checks", "cases": [metadata]}, headers=self.headers())
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def test_workspace_uses_membership_and_retention_not_global_activity(self):
        self.import_report()
        failed = self.import_report(upload=self.failed).json()
        self.create_suite()
        outside = self.make_client()
        self.login(outside, "outsider@example.invalid")
        other = outside.post("/api/team/projects", json={"name": "Other tenant"}, headers=self.headers(outside)).json()
        outside.post("/api/team/projects/" + other["id"] + "/reports", json=self.failed,
            headers={**self.headers(outside), "Idempotency-Key": "9" * 64})
        with self.store.engine.begin() as con:
            con.execute(update(reports).where(reports.c.id == failed["id"]).values(expires_at=utcnow()-timedelta(seconds=1)))
        value = self.client.get("/api/team/workspace").json()
        self.assertEqual(value["totals"], {"projects": 1, "reports": 1, "PASS": 1, "FAIL": 0, "INCONCLUSIVE": 0, "UNSUPPORTED": 0})
        self.assertEqual(value["projects"][0]["suite_count"], 1)
        self.assertEqual(len(value["recent_reports"]), 1)
        self.assertEqual(value["recent_reports"][0]["project_name"], "Private QA")
        self.assertNotIn(other["id"], str(value))
        self.assertEqual(outside.get(self.base + "/insights").status_code, 404)

    def test_workspace_recent_reports_capped_ten_and_no_api_keys(self):
        for index in range(12):
            self.assertEqual(self.import_report(key=f"{index:064x}").status_code, 201)
        value = self.client.get("/api/team/workspace").json()
        self.assertEqual(value["totals"]["reports"], 12)
        self.assertEqual(len(value["recent_reports"]), 10)
        token = self.key()["secret"]
        for path in ("/api/team/workspace", "/api/team/catalog"):
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer " + token}).status_code, 403)
            self.assertEqual(self.make_client().get(path).status_code, 401)

    def test_catalog_hashes_match_actual_manifests_and_empty_is_not_pass(self):
        value = self.client.get("/api/team/catalog").json()
        self.assertEqual(value["count"], 24)
        self.assertEqual(len({case["case_id"] for case in value["items"]}), 24)
        for case in value["items"]:
            self.assertEqual(case["manifest_hash"], content_hash(load_case(case["case_id"])))
        self.create_suite()
        insight = self.client.get(self.base + "/insights").json()
        self.assertEqual(insight["coverage"]["reported_cases"], 0)
        self.assertEqual(len(insight["coverage"]["missing_case_ids"]), 24)
        self.assertEqual(insight["suites"][0]["status"], "INCONCLUSIVE")
        self.assertEqual(insight["suites"][0]["counts"]["MISSING"], 1)

    def test_insights_use_actual_checks_and_latest_compatible_upload(self):
        self.create_suite()
        self.import_report(upload=self.failed)
        insight = self.client.get(self.base + "/insights").json()
        self.assertEqual(insight["suites"][0]["status"], "FAIL")
        self.assertTrue(any("failed checks" in text for text in insight["latest_cases"][0]["recommendations"]))
        self.assertTrue(any("Reuse one operation key" in text for text in insight["latest_cases"][0]["recommendations"]))
        self.assertIn("not an automatic diagnosis", insight["notice"])
        passed = self.import_report().json()
        insight = self.client.get(self.base + "/insights").json()
        self.assertEqual(insight["suites"][0]["status"], "PASS")
        self.assertEqual(insight["suites"][0]["cases"][0]["report_id"], passed["id"])
        self.assertEqual(insight["coverage"]["reported_cases"], 1)
        self.assertEqual(insight["latest_cases"][0]["checks"], self.upload["report"]["checks"])
        self.assertNotIn("events", insight["latest_cases"][0])

    def test_mismatched_version_hash_and_expiry_cannot_pass_readiness(self):
        case = {"case_id": "refund-response-lost", "case_version": "1.0.1", "manifest_hash": "a" * 64}
        self.create_suite(case)
        imported = self.import_report().json()
        result = self.client.get(self.base + "/insights").json()["suites"][0]
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["cases"][0]["status"], "INCOMPATIBLE")
        with self.store.engine.begin() as con:
            con.execute(update(reports).where(reports.c.id == imported["id"]).values(expires_at=utcnow()-timedelta(seconds=1)))
        self.assertEqual(self.client.get(self.base + "/insights").json()["suites"][0]["cases"][0]["status"], "MISSING")

    def test_unknown_and_untriggered_fault_never_pass_snapshot(self):
        case = load_case("refund-response-lost")
        metadata = {"case_id": case["case_id"], "case_version": case["case_version"], "manifest_hash": content_hash(case)}
        suite = {"id": "fixture", "name": "Fixture", "cases": [metadata]}
        row = {"id": "fixture-report", "case_id": case["case_id"], "verdict": "PASS", "upload": copy.deepcopy(self.upload)}
        coverage = next(check for check in row["upload"]["report"]["checks"] if check["id"] == "fault_coverage")
        coverage["status"] = "unknown"
        self.assertEqual(suite_readiness(suite, [row])["status"], "INCONCLUSIVE")
        coverage["status"] = "not_applicable"
        self.assertEqual(suite_readiness(suite, [row])["status"], "INCONCLUSIVE")
        coverage["status"] = "fail"
        self.assertEqual(suite_readiness(suite, [row])["status"], "FAIL")

    def test_invite_hash_fragment_email_binding_reuse_and_role(self):
        invite = self.invite(role="developer")
        token = self.token(invite)
        with self.store.engine.connect() as con:
            saved = con.execute(select(invitations)).mappings().one()
            self.assertEqual(saved["token_hash"], token_hash(token))
            self.assertNotIn(token, str(dict(saved)))
        listing = self.client.get(self.base + "/invitations").json()
        self.assertNotIn("token_hash", str(listing))
        self.assertNotIn(token, str(listing))
        wrong = self.make_client()
        self.login(wrong, "wrong@example.invalid")
        self.assertEqual(self.accept(wrong, token).status_code, 403)
        recipient = self.make_client()
        user = self.login(recipient, "recipient@example.invalid")
        self.assertEqual(self.accept(recipient, token, headers={}).status_code, 403)
        result = self.accept(recipient, token)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {"project_id": self.project, "role": "developer", "already_member": False})
        self.assertEqual(self.accept(recipient, token).status_code, 410)
        self.assertEqual(recipient.get(self.base).json()["role"], "developer")

    def test_invite_permissions_shape_and_cross_tenant_revoke(self):
        viewer, _ = self.member("viewer")
        for method in ("get", "post"):
            call = getattr(viewer, method)
            kwargs = {} if method == "get" else {"json": {"email": "any@example.invalid", "role": "viewer"}, "headers": self.headers(viewer)}
            self.assertEqual(call(self.base + "/invitations", **kwargs).status_code, 403)
        for body in ({"email": "invalid", "role": "viewer"}, {"email": "valid@example.invalid", "role": "owner"},
                {"email": "valid@example.invalid", "role": "viewer", "expires_days": 8}):
            self.assertEqual(self.client.post(self.base + "/invitations", json=body, headers=self.headers()).status_code, 422)
        invite = self.invite()
        other = viewer.post("/api/team/projects", json={"name": "Other"}, headers=self.headers(viewer)).json()["id"]
        self.assertEqual(viewer.delete("/api/team/projects/" + other + "/invitations/" + invite["id"], headers=self.headers(viewer)).status_code, 404)
        self.assertEqual(self.client.get(self.base + "/invitations", headers={"Authorization": "Bearer " + self.key()["secret"]}).status_code, 403)

    def test_expired_revoked_reissued_links_and_demoted_inviter_denied(self):
        recipient = self.make_client()
        self.login(recipient, "recipient@example.invalid")
        first = self.invite()
        second = self.invite()
        self.assertEqual(self.accept(recipient, self.token(first)).status_code, 410)
        self.assertEqual(self.client.delete(self.base + "/invitations/" + second["id"], headers=self.headers()).status_code, 200)
        self.assertEqual(self.accept(recipient, self.token(second)).status_code, 410)
        third = self.invite()
        with self.store.engine.begin() as con:
            con.execute(update(invitations).where(invitations.c.id == third["id"]).values(expires_at=utcnow()-timedelta(seconds=1)))
        self.assertEqual(self.accept(recipient, self.token(third)).status_code, 410)
        fourth = self.invite()
        second_owner, _ = self.member("owner", "secondowner@example.invalid")
        result = second_owner.patch(self.base + "/members/" + self.owner["id"], json={"role": "viewer"}, headers=self.headers(second_owner))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.accept(recipient, self.token(fourth)).status_code, 410)

    def test_invite_acceptance_does_not_change_existing_role(self):
        invite = self.invite(role="viewer")
        recipient, _ = self.member("developer", "recipient@example.invalid")
        result = self.accept(recipient, self.token(invite))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["role"], "developer")
        self.assertTrue(result.json()["already_member"])

    def test_invite_rechecks_seat_capacity_at_acceptance(self):
        invite = self.invite()
        recipient = self.make_client()
        self.login(recipient, "recipient@example.invalid")
        with self.store.engine.begin() as con:
            for index in range(24):
                user = self.store.create_user("https://identity.example.invalid", str(index), f"seat{index}@example.invalid", "Seat fixture")
                con.execute(insert(memberships).values(project_id=self.project, user_id=user["id"], role="viewer"))
        self.assertEqual(self.accept(recipient, self.token(invite)).status_code, 409)
        with self.store.engine.connect() as con:
            row = con.execute(select(invitations).where(invitations.c.id == invite["id"])).mappings().one()
            self.assertIsNone(row["accepted_at"])

    def test_parallel_acceptance_consumes_once(self):
        invite = self.invite()
        client1, client2 = self.make_client(), self.make_client()
        self.login(client1, "recipient@example.invalid")
        self.login(client2, "recipient@example.invalid")
        headers1, headers2 = self.headers(client1), self.headers(client2)
        token = self.token(invite)
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(self.accept, client1, token, headers1)
            two = pool.submit(self.accept, client2, token, headers2)
            self.assertEqual(sorted([one.result().status_code, two.result().status_code]), [200, 410])
        with self.store.engine.connect() as con:
            self.assertEqual(con.execute(select(func.count()).select_from(memberships).where(memberships.c.project_id == self.project)).scalar_one(), 2)

    def test_password_recipient_must_verify_even_in_local_mode(self):
        user = self.store.create_user("breakroom:password", "recipient", "recipient@example.invalid", "Recipient")
        with self.store.engine.begin() as con:
            con.execute(insert(credentials).values(user_id=user["id"], password_hash="unused-no-password-login", verified_at=None, changed_at=utcnow()))
        recipient = self.make_client()
        raw_session = "a" * 43
        self.store.create_session(user["id"], token_hash(raw_session), "csrf-fixture", time.time() + 300)
        recipient.cookies.set(SESSION_COOKIE, raw_session)
        token = self.token(self.invite())
        self.assertEqual(self.accept(recipient, token).status_code, 403)
        with self.store.engine.begin() as con:
            con.execute(update(credentials).where(credentials.c.user_id == user["id"]).values(verified_at=utcnow()))
        self.assertEqual(self.accept(recipient, token).status_code, 200)
