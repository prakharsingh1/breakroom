from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from breakroom.agents import corrected, faulty
from breakroom.evaluator import run_in_process
from breakroom.reports import (compare_reports, export_case, gate_exit, load_bundle,
                               read_json, render_html, render_junit, validate_report, write_bundle)
from breakroom.scenarios import load_case


class ArtifactsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        case = load_case("refund-response-lost")
        cls.bad = run_in_process(case, faulty)
        cls.good = run_in_process(case, corrected)

    def test_round_trip_and_unknown_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            original = deepcopy(self.good)
            original["metrics"]["cost_usd"] = None
            write_bundle([original], folder)
            restored = load_bundle(folder)["reports"][0]
            self.assertEqual(restored, original)
            self.assertIsNone(restored["metrics"]["cost_usd"])

    def test_invalid_report_schema_and_lying_pass(self):
        for mutation in (lambda r: r.update(schema_version="9.0"),
                         lambda r: r.update(payload={"command": "bad"}),
                         lambda r: r.update(verdict="MAYBE"),
                         lambda r: r.update(checks=[]),
                         lambda r: r.update(engine_version='1.0"; import os; #')):
            report = deepcopy(self.good)
            mutation(report)
            with self.assertRaises(ValueError):
                validate_report(report)
        report = deepcopy(self.bad)
        report["verdict"] = "PASS"
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_bounded_and_duplicate_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            for content in ('{"a":1,"a":2}', '{"x":NaN}', '[' * 50 + '0' + ']' * 50):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    read_json(path)
            path.write_text('"' + 'x' * 1024 + '"')
            with self.assertRaises(ValueError):
                read_json(path, max_bytes=100)

    def test_html_is_escaped_and_has_no_executable_report_content(self):
        report = deepcopy(self.good)
        report["checks"][0]["message"] = '<script>alert("secret")</script><img src=x onerror=bad>'
        rendered = render_html([report])
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("Content-Security-Policy", rendered)

    def test_junit_has_actual_failure(self):
        xml = ET.fromstring(render_junit([self.bad, self.good]))
        self.assertEqual(xml.attrib["tests"], "2")
        self.assertEqual(xml.attrib["failures"], "1")
        self.assertEqual(len(xml.findall("testcase/failure")), 1)

    def test_xml_control_characters_remain_parseable(self):
        report = deepcopy(self.bad)
        report["checks"][0]["message"] = "unsafe\x00\x01text"
        parsed = ET.fromstring(render_junit([report]))
        self.assertIsNotNone(parsed.find("testcase/failure"))

    def test_manifest_integrity_is_checked(self):
        report = deepcopy(self.good)
        report["case"]["manifest"]["name"] = "Tampered case title"
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_gate_precedence_and_coverage(self):
        unknown = deepcopy(self.good)
        unknown["verdict"] = "INCONCLUSIVE"
        self.assertEqual(gate_exit([]), 2)
        self.assertEqual(gate_exit([self.good]), 0)
        self.assertEqual(gate_exit([self.good], ["not-run"]), 2)
        self.assertEqual(gate_exit([unknown]), 2)
        self.assertEqual(gate_exit([unknown, self.bad], ["not-run"]), 1)

    def test_comparison_rejects_versions_and_unpaired_trials(self):
        self.assertTrue(compare_reports([self.bad], [self.good])["compatible"])
        candidate = deepcopy(self.good)
        candidate["oracle_version"] = "2.0"
        comparison = compare_reports([self.bad], [candidate])
        self.assertFalse(comparison["compatible"])
        self.assertIn("oracle_version", comparison["pairs"][0]["incompatibilities"])
        self.assertIsNone(comparison["pairs"][0]["changed_effects"])
        self.assertFalse(compare_reports([self.bad], [])["compatible"])

    def test_repeated_trial_pairing_is_explicit(self):
        comparison = compare_reports([self.bad, self.bad], [self.good, self.good])
        self.assertTrue(comparison["compatible"])
        self.assertEqual([p["trial_index"] for p in comparison["pairs"]], [1, 2])
        self.assertFalse(compare_reports([self.bad, self.bad], [self.good])["compatible"])

    def test_missing_required_checks_are_invalid(self):
        report = deepcopy(self.good)
        report["checks"] = [c for c in report["checks"] if c["id"] != "fault_coverage"]
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_export_executes_good_and_bad_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            export_case(self.bad, folder)
            test = Path(folder) / "test_regression.py"
            good = subprocess.run([sys.executable, str(test), "--agent", "breakroom.agents:corrected"], capture_output=True, text=True)
            bad = subprocess.run([sys.executable, str(test), "--agent", "breakroom.agents:faulty"], capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(bad.returncode, 1, bad.stderr)
            self.assertIn("FAIL", bad.stderr)


if __name__ == "__main__":
    unittest.main()
