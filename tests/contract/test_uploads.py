from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from breakroom.agents import corrected, faulty
from breakroom.cli import main
from breakroom.evaluator import run_in_process
from breakroom.reports import compare_reports, read_json, validate_report, write_bundle
from breakroom.runner import run_case
from breakroom.scenarios import content_hash, list_cases, load_case
from breakroom.uploads import (MAX_RESPONSE_BYTES, MAX_UPLOAD_BYTES, REDACTED, _NoRedirect,
                              _server_url, load_redaction_key, prepare_upload, save_prepared_upload,
                              upload_digest, upload_prepared_file, validate_upload_envelope)


class UploadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = bytes(range(32))
        cls.bad = run_in_process(load_case("refund-response-lost"), faulty)
        cls.good = run_in_process(load_case("refund-response-lost"), corrected)
        assert cls.bad["verdict"] == "FAIL" and cls.good["verdict"] == "PASS"

    def prepared(self, report=None):
        return prepare_upload(report or self.good, redaction_key=self.key)

    def test_preserves_actual_outcomes_and_compatible_comparison(self):
        bad, good = self.prepared(self.bad)["report"], self.prepared()["report"]
        self.assertEqual((bad["verdict"], good["verdict"]), ("FAIL", "PASS"))
        self.assertEqual((len(bad["final_state"]["refunds"]), len(good["final_state"]["refunds"])), (2, 1))
        self.assertEqual(bad["metrics"]["refunded_minor_by_currency"], {"INR": 200000})
        self.assertEqual(good["metrics"]["refunded_minor_by_currency"], {"INR": 100000})
        self.assertTrue(compare_reports([bad], [good])["compatible"])
        self.assertFalse(compare_reports([bad], [prepare_upload(self.good, redaction_key=b"x" * 32)["report"]])["compatible"])

    def test_cited_event_ids_remain_resolvable_and_relationships_stable(self):
        original = deepcopy(self.good)
        event_id = original["events"][0]["id"]
        original["checks"][0]["evidence_refs"] = [event_id]
        original["agent_result"]["evidence_refs"] = [event_id]
        minimized = self.prepared(original)["report"]
        mapping = {before["id"]: after["id"] for before, after in zip(original["events"], minimized["events"])}
        self.assertEqual(minimized["checks"][0]["evidence_refs"], [mapping[event_id]])
        self.assertEqual(minimized["agent_result"]["evidence_refs"], [mapping[event_id]])
        for before, after in zip(original["checks"], minimized["checks"]):
            for ref, opaque in zip(before["evidence_refs"], after["evidence_refs"]):
                if ref in mapping:
                    self.assertEqual(opaque, mapping[ref])
        self.assertEqual(minimized["final_state"]["refunds"][0]["order_id"], minimized["initial_state"]["orders"][0]["id"])

    def test_free_text_and_payloads_are_removed_without_mutating_original(self):
        report = deepcopy(self.good)
        secret = "PRIVATE-provider-token-sourcecode-customer@example.test"
        report["checks"][0]["message"] = secret
        report["agent"]["reference"] = secret
        report["agent"]["source"] = secret
        report["agent_result"]["customer_text"] = secret
        report["agent_result"]["claims"][0]["source_code"] = secret
        report["events"][0]["data"] = {"arguments": {"token": secret}, "response": {"customer_text": secret}, "id": secret}
        report["final_state"]["customers"][0]["display_name"] = secret
        report["final_state"]["tickets"][0]["messages"] = [{"text": secret, "source": secret}]
        before = deepcopy(report)
        prepared = self.prepared(report)
        self.assertNotIn(secret, json.dumps(prepared))
        self.assertEqual(report, before)
        self.assertNotIn("arguments", prepared["report"]["events"][0]["data"])
        self.assertNotIn("source", prepared["report"]["agent"])
        self.assertEqual(prepared["report"]["checks"][0]["status"], report["checks"][0]["status"])

    def test_original_and_materialized_provenance_are_distinguished(self):
        report = run_case(load_case("duplicate-event-delivery"), "breakroom.agents:corrected", seed=2)
        self.assertEqual(report["verdict"], "PASS")
        prepared = self.prepared(report)
        privacy, minimized = prepared["privacy"], prepared["report"]
        canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self.assertEqual(privacy["original_report_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(privacy["original_source_manifest_hash"], report["case"]["source_manifest_hash"])
        self.assertEqual(privacy["original_generator_version"], report["case"]["generator_version"])
        self.assertNotIn("source_manifest", minimized["case"])
        self.assertNotIn("generator_version", minimized["case"])
        self.assertNotEqual(minimized["case"]["manifest_hash"], privacy["original_case_manifest_hash"])
        self.assertEqual(minimized["case"]["manifest_hash"], content_hash(minimized["case"]["manifest"]))
        self.assertEqual(validate_report(minimized), minimized)
        self.assertEqual(minimized["case"]["manifest"]["variation_constraints"]["supported_seeds"], [2])

    def test_all_24_executed_corrected_contracts_can_be_prepared(self):
        for case in list_cases():
            with self.subTest(case=case["case_id"]):
                report = run_in_process(case, corrected)
                self.assertEqual(report["verdict"], "PASS")
                self.assertEqual(self.prepared(report)["report"]["verdict"], "PASS")

    def test_unknown_evidence_stays_unknown(self):
        report = deepcopy(self.good)
        report["final_state"] = {"available": False, "reason": "database secret path", "clock": None}
        report["metrics"] = {"refund_count": None, "refunded_minor_by_currency": None}
        report["agent_result"] = {}
        report["verdict"] = "INCONCLUSIVE"
        result = self.prepared(report)["report"]
        self.assertEqual(result["final_state"], {"available": False, "reason": REDACTED, "clock": None})
        self.assertIsNone(result["metrics"]["refund_count"])
        self.assertIsNone(result["metrics"]["refunded_minor_by_currency"])
        self.assertIsNone(result["agent_result"]["claims"])
        self.assertIsNone(result["agent_result"]["escalated"])
        self.assertEqual(result["verdict"], "INCONCLUSIVE")

    def test_private_case_and_customer_metadata_are_minimized(self):
        case = load_case("normal-refund")
        case["case_id"] = "private-client-workflow"
        case["name"] = "Private client contract"
        case["task"]["text"] = "Private customer instructions"
        case["sources"] = ["https://private.example/secret-source"]
        case["initial_state"]["customers"][0]["display_name"] = "Private Customer Name"
        report = run_in_process(case, corrected)
        self.assertEqual(report["verdict"], "PASS")
        prepared = self.prepared(report)
        encoded = json.dumps(prepared)
        for value in ("private-client-workflow", "Private client contract", "Private customer instructions", "https://private.example", "Private Customer Name"):
            self.assertNotIn(value, encoded)
        self.assertRegex(prepared["report"]["case"]["case_id"], r"^anon-[0-9a-f]{32}$")

    def test_server_rejects_raw_reports_flags_and_unrecognized_fields(self):
        mutations = [lambda p: p.update(extra="secret"),
                     lambda p: p.update(report=deepcopy(self.good)),
                     lambda p: p["privacy"].update(format="raw-is-safe"),
                     lambda p: p["privacy"].update(approved=True),
                     lambda p: p["report"]["agent"].update(source="print(secret)"),
                     lambda p: p["report"]["checks"][0].update(message="private customer name"),
                     lambda p: p["report"]["events"][0]["data"].update(url="https://private.example/secret"),
                     lambda p: p["report"]["final_state"].update(clock="secret"),
                     lambda p: p["report"]["final_state"]["refunds"][0].update(status={"secret": 1})]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = self.prepared()
                mutation(payload)
                with self.assertRaises(ValueError):
                    validate_upload_envelope(payload)

    def test_recomputed_checksums_do_not_bypass_minimization(self):
        payload = self.prepared()
        case = payload["report"]["case"]
        case["manifest"]["task"]["text"] = "Raw private customer instruction"
        case["manifest_hash"] = content_hash(case["manifest"])
        validate_report(payload["report"])
        with self.assertRaises(ValueError):
            validate_upload_envelope(payload)

    def test_malformed_and_oversize_envelopes_are_validation_errors(self):
        for payload in (None, [], {}, {"report": "x" * MAX_UPLOAD_BYTES}, {"x": float("nan")}):
            with self.subTest(payload=type(payload)), self.assertRaises(ValueError):
                validate_upload_envelope(payload)
        payload = self.prepared()
        payload["privacy"]["original_report_sha256"] = "invalid"
        with self.assertRaises(ValueError):
            validate_upload_envelope(payload)

    def test_local_key_is_stable_private_and_never_serialized(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private/key"
            key = load_redaction_key(path)
            self.assertEqual(key, load_redaction_key(path))
            self.assertEqual(len(key), 32)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(key.hex(), json.dumps(prepare_upload(self.good, redaction_key=key)))
            link = Path(folder) / "link"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                load_redaction_key(link)
            path.write_bytes(b"bad")
            with self.assertRaises(ValueError):
                load_redaction_key(path)

    def test_prepare_cli_is_local_and_selects_recorded_case_trial(self):
        with tempfile.TemporaryDirectory() as folder, patch("breakroom.uploads.build_opener") as opener:
            source, destination, key = Path(folder) / "bundle", Path(folder) / "prepared.json", Path(folder) / "key"
            write_bundle([self.bad, self.good], source)
            with redirect_stdout(io.StringIO()) as out:
                code = main(["prepare-upload", str(source), "--case", "refund-response-lost", "--trial", "2", "--out", str(destination), "--redaction-key-file", str(key)])
            self.assertEqual(code, 0)
            self.assertIn("No report was uploaded", out.getvalue())
            self.assertEqual(read_json(destination)["report"]["verdict"], "PASS")
            opener.assert_not_called()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["prepare-upload", str(source), "--case", "missing", "--out", str(destination)]), 2)


class FakeResponse:
    def __init__(self, body, status=201):
        self.body, self.status, self.read_limit = body, status, None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]


class UploadTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = prepare_upload(run_in_process(load_case("normal-refund"), corrected), redaction_key=b"a" * 32)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = save_prepared_upload(self.payload, Path(self.folder.name) / "prepared.json")
        self.env = patch.dict(os.environ, {"BREAKROOM_TEST_KEY": "not-a-real-provider-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def upload(self, **kwargs):
        options = {"server": "https://team.example", "project_id": "project_1", "token_env": "BREAKROOM_TEST_KEY"}
        options.update(kwargs)
        return upload_prepared_file(self.path, **options)

    def ack(self, **kwargs):
        value = {"id": "report_1", "project_id": "project_1", "provenance": "customer_generated", "duplicate": False}
        value.update(kwargs)
        return json.dumps(value).encode()

    def test_explicit_request_has_bounded_transport_and_idempotency(self):
        response = FakeResponse(self.ack())
        with patch("breakroom.uploads.build_opener") as builder:
            builder.return_value.open.return_value = response
            result = self.upload()
            request = builder.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://team.example/api/team/projects/project_1/reports")
            self.assertEqual(request.get_header("Authorization"), "Bearer not-a-real-provider-secret")
            self.assertEqual(request.get_header("Idempotency-key"), upload_digest(self.payload))
            self.assertNotIn("not-a-real-provider-secret", request.full_url)
            self.assertEqual(builder.return_value.open.call_args.kwargs, {"timeout": 10})
            self.assertEqual(builder.call_args.args[0].proxies, {})
            self.assertIsInstance(builder.call_args.args[1], _NoRedirect)
            self.assertEqual(response.read_limit, MAX_RESPONSE_BYTES + 1)
            self.assertEqual(result, {"id": "report_1", "project_id": "project_1", "duplicate": False, "status": 201})

    def test_redirects_never_forward_bearer_and_fail_without_secret_echo(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))
        with patch("breakroom.uploads.build_opener") as builder:
            builder.return_value.open.side_effect = HTTPError("https://team.example", 302, "not-a-real-provider-secret", {}, None)
            with self.assertRaises(ValueError) as error:
                self.upload()
            self.assertIn("302", str(error.exception))
            self.assertNotIn("not-a-real-provider-secret", str(error.exception))
            self.assertEqual(builder.return_value.open.call_count, 1)

    def test_response_bounds_errors_and_ack_ownership(self):
        cases = [FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 1)), FakeResponse(b"<html>error</html>"),
                 FakeResponse(self.ack(project_id="another_project")), FakeResponse(self.ack(provenance="certified")),
                 FakeResponse(self.ack(id="https://untrusted.example")), FakeResponse(self.ack(id="not-a-real-provider-secret")), FakeResponse(self.ack(), 202)]
        for response in cases:
            with self.subTest(body=response.body[:50]), patch("breakroom.uploads.build_opener") as builder:
                builder.return_value.open.return_value = response
                with self.assertRaises(ValueError):
                    self.upload()
        for error in (URLError("secret"), TimeoutError("secret")):
            with patch("breakroom.uploads.build_opener") as builder:
                builder.return_value.open.side_effect = error
                with self.assertRaises(ValueError) as captured:
                    self.upload()
                self.assertNotIn("secret", str(captured.exception))

    def test_origins_require_https_and_explicit_local_http(self):
        for server in ("http://team.example", "http://127.0.0.1:8001", "https://u:secret@team.example", "https://team.example/path", "https://team.example?q=1", "https://team.example/#fragment", "https://team.example:99999", "\nhttps://team.example", "https://team.example\n.evil"):
            with self.subTest(server=server), self.assertRaises(ValueError):
                _server_url(server, "project_1", False)
        for server in ("http://127.0.0.1:8001", "http://localhost:8001", "http://[::1]:8001"):
            self.assertTrue(_server_url(server, "project_1", True).endswith("/projects/project_1/reports"))
        with self.assertRaises(ValueError):
            _server_url("http://other.example", "project_1", True)
        with self.assertRaises(ValueError):
            _server_url("https://team.example", "../another", False)

    def test_token_is_environment_only_and_invalid_files_never_reach_network(self):
        with patch("breakroom.uploads.build_opener") as builder:
            with self.assertRaises(ValueError):
                self.upload(expected_sha256="0" * 64)
            for token in ("", "secret\nHeader:value", "secret\x7f", "secreté"):
                with patch.dict(os.environ, {"BREAKROOM_TEST_KEY": token}), self.assertRaises(ValueError):
                    self.upload()
            with self.assertRaises(ValueError):
                self.upload(token_env="a-token-value")
            for body in ('{"x":1,"x":2}', '{"x":NaN}', '[' * 50 + '0' + ']' * 50, 'x' * (MAX_UPLOAD_BYTES + 1)):
                self.path.write_text(body)
                with self.assertRaises(ValueError):
                    self.upload()
            builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
