from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from breakroom.coverage import classify_observation, execute_matrix, summarize_cells, write_matrix
from breakroom.reports import load_bundle
from breakroom.runner import run_case
from breakroom.scenarios import load_case


class CoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        normal = load_case("normal-refund")
        lost = load_case("refund-response-lost")
        # A focused synthetic mapping keeps this contract test independent of
        # future additional pack controls; no production assertion is removed.
        normal["negative_controls"] = ["always_escalate", "success_without_evidence"]
        lost["negative_controls"] = ["faulty"]
        cls.cases = [normal, lost]
        cls.matrix, cls.reports = execute_matrix(cls.cases)
        cls.good = next(report for report in cls.reports if report["verdict"] == "PASS")
        cls.bad = next(report for report in cls.reports if report["case"]["case_id"] == "refund-response-lost" and report["verdict"] == "FAIL")

    def test_actual_controls_have_calculated_denominators(self):
        summary = self.matrix["summary"]
        self.assertEqual(summary["detection_fraction"], {"numerator": 3, "denominator": 3})
        self.assertEqual(summary["expected_corrected_trials"], 2)
        self.assertEqual(summary["passing_corrected_trials"], 2)
        self.assertEqual(summary["executed_trial_count"], 5)
        self.assertEqual(summary["not_scheduled_cells"], 3)
        self.assertEqual(summary["gate_exit"], 0)
        self.assertEqual(len(self.reports), 5)
        for cell in self.matrix["cells"]:
            if cell["report_index"] is not None:
                self.assertEqual(cell["observed_verdict"], self.reports[cell["report_index"]]["verdict"])

    def test_detections_require_real_effect_checks_and_fault_coverage(self):
        observation = classify_observation(self.bad, "FAIL")
        self.assertEqual(observation["status"], "detected")
        self.assertIn("no_duplicate_effects", observation["applicable_failed_checks"])
        report = deepcopy(self.bad)
        for check in report["checks"]:
            if check["id"] == "fault_coverage":
                check["status"] = "unknown"
        self.assertEqual(classify_observation(report, "FAIL")["status"], "incomplete")

    def test_unknown_and_unsupported_never_become_detections(self):
        for verdict in ("INCONCLUSIVE", "UNSUPPORTED"):
            report = deepcopy(self.good)
            report["verdict"] = verdict
            report["checks"] = [dict(check, status="unknown") if check["id"] == "adapter_capabilities" else check for check in report["checks"]]
            observation = classify_observation(report, "FAIL")
            self.assertEqual(observation["status"], "incomplete")
            self.assertFalse(observation["observed_detection"])

    def test_actual_untriggered_fault_mapping_stays_in_denominator(self):
        case = deepcopy(self.cases[1])
        case["negative_controls"] = ["success_without_evidence"]
        matrix, reports = execute_matrix([case])
        self.assertEqual(matrix["summary"]["detection_fraction"], {"numerator": 0, "denominator": 1})
        self.assertEqual(matrix["summary"]["incomplete_negative_trials"], 1)
        self.assertEqual(matrix["summary"]["gate_exit"], 2)
        self.assertTrue(any(report["verdict"] == "FAIL" for report in reports))

    def test_manifest_control_names_cannot_expand_the_import_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "should-not-exist"
            case = deepcopy(self.cases[0])
            case["negative_controls"] = [f"__import__('pathlib').Path({str(marker)!r}).touch()"]
            calls = []
            def recording(case, reference, **kwargs):
                calls.append(reference)
                return run_case(case, reference, **kwargs)
            matrix, reports = execute_matrix([case], runner=recording)
            self.assertEqual(calls, ["breakroom.agents:corrected"])
            self.assertFalse(marker.exists())
            self.assertEqual(len(reports), 1)
            self.assertEqual(matrix["summary"]["expected_negative_trials"], 1)
            self.assertEqual(matrix["summary"]["incomplete_negative_trials"], 1)
            self.assertEqual(matrix["summary"]["gate_exit"], 2)

    def test_missing_mapping_is_incomplete_not_a_vacuous_pass(self):
        case = deepcopy(self.cases[0])
        case["negative_controls"] = []
        matrix, _ = execute_matrix([case])
        self.assertEqual(matrix["summary"]["gate_exit"], 2)
        self.assertEqual(matrix["summary"]["missing_negative_control_cases"], [case["case_id"]])
        self.assertEqual(matrix["summary"]["detection_fraction"], {"numerator": 0, "denominator": 0})

    def test_a_mutant_that_actually_passes_is_a_missed_detection(self):
        case = deepcopy(self.cases[0])
        case["negative_controls"] = ["faulty", "unregistered_control"]
        matrix, reports = execute_matrix([case])
        self.assertTrue(all(report["verdict"] == "PASS" for report in reports))
        self.assertEqual(matrix["summary"]["detection_fraction"], {"numerator": 0, "denominator": 2})
        self.assertEqual(matrix["summary"]["missed_negative_trials"], 1)
        self.assertEqual(matrix["summary"]["incomplete_negative_trials"], 1)
        self.assertEqual(matrix["summary"]["gate_exit"], 1)

    def test_repeats_are_distinct_real_trials(self):
        matrix, reports = execute_matrix([self.cases[1]], trials=2)
        self.assertEqual(matrix["summary"]["expected_negative_trials"], 2)
        self.assertEqual(matrix["summary"]["expected_corrected_trials"], 2)
        self.assertEqual(matrix["summary"]["executed_trial_count"], 4)
        self.assertEqual(len({report["run_id"] for report in reports}), 4)
        self.assertEqual({cell["trial_index"] for cell in matrix["cells"]}, {1, 2})
        self.assertTrue(all(report["initial_state"]["refunds"] == [] for report in reports))

    def test_varied_trials_preserve_source_and_materialized_hashes(self):
        matrix, reports = execute_matrix([self.cases[1]], seeds=(0, 1, 2))
        self.assertEqual(matrix["summary"]["detection_fraction"], {"numerator": 3, "denominator": 3})
        self.assertEqual(matrix["summary"]["passing_corrected_trials"], 3)
        self.assertEqual(len({report["case"]["source_manifest_hash"] for report in reports}), 1)
        self.assertEqual(len({report["case"]["fixture_hash"] for report in reports}), 3)
        self.assertTrue(all(cell["generator_version"] is not None for cell in matrix["cells"]))
        with tempfile.TemporaryDirectory() as folder:
            write_matrix(matrix, reports, folder)
            restored = load_bundle(Path(folder) / "reports")
            self.assertEqual(restored["reports"], reports)

    def test_infrastructure_exception_does_not_report_a_detected_mutant(self):
        def crashed(*args, **kwargs):
            raise OSError("synthetic worker unavailable")
        matrix, reports = execute_matrix([self.cases[1]], runner=crashed)
        self.assertEqual(reports, [])
        self.assertEqual(matrix["summary"]["incomplete_negative_trials"], 1)
        self.assertEqual(matrix["summary"]["gate_exit"], 2)
        self.assertEqual(matrix["summary"]["executed_trial_count"], 0)

    def test_all_pairs_runs_unmapped_cells_but_does_not_inflate_denominator(self):
        matrix, reports = execute_matrix(self.cases, all_pairs=True)
        self.assertEqual(matrix["summary"]["executed_trial_count"], 8)
        self.assertEqual(matrix["summary"]["expected_negative_trials"], 3)
        self.assertEqual(matrix["summary"]["not_scheduled_cells"], 0)
        self.assertEqual(sum(cell["status"] == "observed" for cell in matrix["cells"]), 3)
        self.assertEqual(len(reports), 8)

    def test_rejected_configuration_is_bounded_before_execution(self):
        cases = [self.cases[1]]
        for kwargs in ({"trials": 0}, {"trials": True}, {"seeds": (0, 0)}, {"seeds": (-1,)}, {"seeds": ([],)},
                       {"seeds": (9,)}, {"timeout": float("nan")}, {"all_pairs": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                execute_matrix(cases, **kwargs)
        with self.assertRaises(ValueError):
            execute_matrix([])
        with self.assertRaises(ValueError):
            execute_matrix(cases + cases)
        large = []
        for index in range(100):
            case = deepcopy(self.cases[0])
            case["case_id"] = f"bounded-{index}"
            large.append(case)
        with self.assertRaises(ValueError):
            execute_matrix(large, trials=2)

    def test_failed_corrected_or_missed_detection_precedes_incomplete_gate(self):
        cells = deepcopy(self.matrix["cells"])
        target = next(cell for cell in cells if cell["expectation"] == "FAIL")
        target["status"] = "missed"
        next(cell for cell in cells if cell["expectation"] == "PASS")["status"] = "incomplete"
        self.assertEqual(summarize_cells(cells, missing_negative_control_cases=[])["gate_exit"], 1)

    def test_saved_matrix_and_existing_bundle_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            write_matrix(self.matrix, self.reports, folder)
            saved = json.loads((Path(folder) / "matrix.json").read_text())
            bundle = load_bundle(Path(folder) / "reports")
            self.assertEqual(saved, self.matrix)
            self.assertEqual(bundle["reports"], self.reports)
            self.assertEqual(bundle["gate_exit"], 1)  # Includes deliberate failures.
            self.assertEqual(saved["summary"]["gate_exit"], 0)  # Pack detected them.
            self.assertTrue((Path(folder) / "reports" / "junit.xml").is_file())

    def test_forged_summary_or_missing_cells_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            matrix = deepcopy(self.matrix)
            matrix["summary"]["detected_negative_trials"] = 999
            with self.assertRaises(ValueError):
                write_matrix(matrix, self.reports, folder)
            matrix = deepcopy(self.matrix)
            matrix["cells"].pop()
            with self.assertRaises(ValueError):
                write_matrix(matrix, self.reports, folder)
            matrix = deepcopy(self.matrix)
            matrix["cells"][0]["observed_verdict"] = "FAIL"
            with self.assertRaises(ValueError):
                write_matrix(matrix, self.reports, folder)

    def test_mutation_cli_executes_and_writes_actual_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-m", "breakroom", "mutation-check", "--case", "normal-refund", "--out", folder]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("not a passing release gate", result.stdout)
            saved = json.loads((Path(folder) / "matrix.json").read_text())
            self.assertGreater(saved["summary"]["executed_trial_count"], 0)


if __name__ == "__main__":
    unittest.main()
