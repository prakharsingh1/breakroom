import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from breakroom.runner import run_case
from breakroom.scenarios import load_case


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.case = load_case("refund-response-lost")

    def test_isolated_reference_controls(self):
        bad = run_case(self.case, "examples.agents.faulty:run")
        good = run_case(self.case, "examples.agents.corrected:run")
        self.assertEqual(bad["verdict"], "FAIL")
        self.assertEqual(good["verdict"], "PASS")
        self.assertEqual(len(bad["final_state"]["refunds"]), 2)
        self.assertEqual(len(good["final_state"]["refunds"]), 1)

    def test_builtin_worker_does_not_inherit_service_secrets(self):
        with patch.dict(os.environ, {key: "synthetic-test-secret" for key in ("STRIPE_SECRET_KEY", "OPENAI_API_KEY", "ZENDESK_TOKEN", "AWS_SECRET_ACCESS_KEY")}):
            report = run_case(self.case, "tests.contract.runner_fixtures:no_secrets")
        self.assertEqual(report["verdict"], "PASS", report["execution"])

    def test_wall_timeout_and_infrastructure_recovery(self):
        started = time.monotonic()
        report = run_case(self.case, "tests.contract.runner_fixtures:hung", timeout=0.4)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(report["execution"]["status"], "timed_out")
        self.assertEqual(report["verdict"], "INCONCLUSIVE")
        crashed = run_case(self.case, "tests.contract.runner_fixtures:crash")
        self.assertEqual(crashed["execution"]["status"], "errored")
        self.assertEqual(crashed["verdict"], "INCONCLUSIVE")

    def test_cancellation_is_bounded(self):
        signal = threading.Event()
        timer = threading.Timer(0.3, signal.set)
        timer.start()
        try:
            report = run_case(self.case, "tests.contract.runner_fixtures:hung", timeout=5, cancel_signal=signal)
        finally:
            timer.cancel()
        self.assertEqual(report["execution"]["status"], "cancelled")
        self.assertEqual(report["verdict"], "INCONCLUSIVE")

    def test_cancel_before_worker_start_preserves_unknown_state(self):
        signal = threading.Event()
        signal.set()
        report = run_case(self.case, "examples.agents.corrected:run", cancel_signal=signal)
        self.assertEqual(report["execution"]["status"], "cancelled")
        self.assertEqual(report["verdict"], "INCONCLUSIVE")
        self.assertFalse(report["final_state"]["available"])
        self.assertIsNone(report["metrics"]["refund_count"])

    def test_missing_adapter_import_has_honest_recovery(self):
        report = run_case(self.case, "no_such_breakroom_test_module:run")
        self.assertEqual(report["execution"]["status"], "errored")
        self.assertEqual(report["verdict"], "INCONCLUSIVE")
        self.assertFalse(report["final_state"]["available"])

    def test_known_failure_survives_lost_worker(self):
        report = run_case(self.case, "tests.contract.runner_fixtures:duplicate_then_hang", timeout=0.6)
        self.assertEqual(report["execution"]["status"], "timed_out")
        self.assertEqual(report["verdict"], "FAIL", report["checks"])
        self.assertGreaterEqual(len(report["final_state"]["refunds"]), 2)

    def test_child_group_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            self.case["task"]["text"] = str(pid_file)
            report = run_case(self.case, "tests.contract.runner_fixtures:spawn_child", timeout=0.8)
            self.assertEqual(report["execution"]["status"], "timed_out")
            self.assertTrue(pid_file.exists())
            pid = pid_file.read_text().strip()
            deadline = time.monotonic() + 1
            while True:
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    break
                self.assertLess(time.monotonic(), deadline, "Child process survived worker cleanup")
                time.sleep(0.02)

    def test_output_flood_is_bounded(self):
        report = run_case(self.case, "tests.contract.runner_fixtures:flood", timeout=3)
        self.assertEqual(report["execution"]["status"], "errored")
        self.assertIn("output", report["execution"]["error"].lower())

    def test_reference_and_provider_configuration_validation(self):
        for agent in ("__import__('os').system('bad')", "https://example.com/agent.py", "foo:run;ls"):
            with self.assertRaises(ValueError):
                run_case(self.case, agent)
        with self.assertRaises(ValueError):
            run_case(self.case, "examples.agents.corrected:run", env_allowlist=("OPENAI_API_KEY",))
        for timeout in (float("nan"), 0, 121):
            with self.assertRaises(ValueError):
                run_case(self.case, "examples.agents.corrected:run", timeout=timeout)


if __name__ == "__main__":
    unittest.main()
