"""A full executed matrix must round-trip through bounded report artifacts."""
import json
from pathlib import Path
import tempfile
import unittest

from breakroom import agents
from breakroom.coverage import execute_matrix
from breakroom.evaluator import run_in_process
from breakroom.reports import (MAX_BUNDLE_BYTES, MAX_BUNDLE_NODES, MAX_JSON_NODES,
    load_bundle, read_json, validate_report, write_bundle)
from breakroom.scenarios import list_cases, load_case


def direct_builtin(case, reference, *, seed, timeout, **kwargs):
    # Only execute_matrix's audited built-in registry calls this test runner.
    agent = getattr(agents, reference.split(":", 1)[1])
    return run_in_process(case, agent, seed, deadline_seconds=timeout,
        agent_metadata={"name": agent.__name__, "reference": reference,
                        "version": None, "code_hash": None, "model": None})


def node_count(value):
    pending, count = [value], 0
    while pending:
        item = pending.pop()
        count += 1
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return count


class MatrixBundleTests(unittest.TestCase):
    def test_full_477_executed_trial_matrix_round_trips(self):
        matrix, reports = execute_matrix(list_cases(), seeds=(0, 1, 2), trials=3, runner=direct_builtin)
        self.assertEqual(matrix["summary"]["gate_exit"], 0, matrix["summary"])
        self.assertEqual(len(reports), 477)
        self.assertEqual(len({report["run_id"] for report in reports}), 477)
        with tempfile.TemporaryDirectory() as folder:
            original = write_bundle(reports, folder)
            path = Path(folder) / "report.json"
            self.assertLess(path.stat().st_size, MAX_BUNDLE_BYTES)
            self.assertGreater(node_count(original), MAX_JSON_NODES)
            self.assertLess(node_count(original), MAX_BUNDLE_NODES)
            restored = load_bundle(path)
            self.assertEqual(restored, original)
            self.assertEqual(restored["trial_count"], 477)
            self.assertEqual(restored["gate_exit"], 1)  # Deliberate mutant failures remain in the release gate.

    def test_individual_and_aggregate_node_caps_remain_finite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            content = [[0] * 20000 for _ in range(13)]
            path.write_text(json.dumps(content))
            with self.assertRaisesRegex(ValueError, "node/depth"):
                read_json(path)
            self.assertEqual(read_json(path, max_bytes=MAX_BUNDLE_BYTES, max_nodes=MAX_BUNDLE_NODES), content)
            path.write_text(json.dumps([[0] * 20000 for _ in range(50)]))
            self.assertLess(path.stat().st_size, MAX_BUNDLE_BYTES)
            with self.assertRaisesRegex(ValueError, "node/depth"):
                read_json(path, max_bytes=MAX_BUNDLE_BYTES, max_nodes=MAX_BUNDLE_NODES)
            for budget in (0, -1, True, MAX_BUNDLE_NODES + 1):
                with self.subTest(budget=budget), self.assertRaises(ValueError):
                    read_json(path, max_nodes=budget)

    def test_writer_rejects_unreadable_aggregate_before_creating_files(self):
        report = run_in_process(load_case("normal-refund"), agents.corrected)
        report["metrics"]["bounded_fixture_values"] = [[0] * 20000 for _ in range(11)]
        validate_report(report)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "must-not-be-written"
            with self.assertRaisesRegex(ValueError, "node/depth"):
                write_bundle([report] * 5, destination)
            self.assertFalse(destination.exists())

    def test_depth_and_trial_caps_are_not_relaxed_for_bundles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "deep.json"
            path.write_text("[" * 26 + "0" + "]" * 26)
            with self.assertRaisesRegex(ValueError, "node/depth"):
                read_json(path, max_bytes=MAX_BUNDLE_BYTES, max_nodes=MAX_BUNDLE_NODES)
            with self.assertRaisesRegex(ValueError, "500 trials"):
                write_bundle([{}] * 501, Path(folder) / "too-many")


if __name__ == "__main__":
    unittest.main()
