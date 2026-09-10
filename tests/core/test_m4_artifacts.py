import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from breakroom import agents
from breakroom.evaluator import run_in_process
from breakroom.reports import export_case, validate_report, write_bundle, load_bundle
from breakroom.scenarios import load_case


class M4ArtifactTests(unittest.TestCase):
    def test_noncanonical_event_export_reproduces_and_detects_mutant(self):
        report = run_in_process(load_case("duplicate-event-delivery"), agents.corrected, seed=2)
        with tempfile.TemporaryDirectory() as temporary:
            exported = export_case(report, temporary)
            script = str(exported / "test_regression.py")
            good = subprocess.run([sys.executable, script, "--agent", "breakroom.agents:corrected"], capture_output=True, text=True, timeout=10)
            bad = subprocess.run([sys.executable, script, "--agent", "breakroom.agents:handle_event_twice"], capture_output=True, text=True, timeout=10)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(bad.returncode, 1, bad.stderr)
            self.assertIn("event_dedup", bad.stderr)

    def test_source_and_materialized_integrity_are_both_verified(self):
        report = run_in_process(load_case("refund-response-lost"), agents.corrected, seed=2)
        self.assertNotEqual(report["case"]["source_manifest_hash"], report["case"]["manifest_hash"])
        for mutation in (
            lambda r: r["case"].update(source_manifest_hash="0"*64),
            lambda r: r["case"]["source_manifest"]["task"].update(amount_minor=123),
            lambda r: r["case"].update(generator_version="unknown"),
            lambda r: r["case"].pop("source_manifest"),
            lambda r: r.update(seed=1),
        ):
            changed = copy.deepcopy(report)
            mutation(changed)
            with self.assertRaises(ValueError):
                validate_report(changed)

    def test_seeded_bundle_roundtrip_preserves_materialization(self):
        report = run_in_process(load_case("concurrent-same-request"), agents.corrected, seed=1)
        with tempfile.TemporaryDirectory() as temporary:
            write_bundle([report], temporary)
            loaded = load_bundle(temporary)["reports"][0]
        self.assertEqual(loaded["case"], report["case"])
        self.assertEqual(loaded["final_state"], report["final_state"])

    def test_legacy_task_mutation_cannot_rewrite_evaluator_authorization(self):
        case = load_case("normal-refund")
        def mutate_policy(task, tools, context):
            task.policy["allow_escalation"] = True
            task.policy["refund_authorized"] = False
            return agents.always_escalate(task, tools, context)
        report = run_in_process(case, mutate_policy)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertFalse(report["case"]["manifest"]["task"]["policy"]["allow_escalation"])
        self.assertTrue(report["case"]["manifest"]["task"]["policy"]["refund_authorized"])


if __name__ == "__main__":
    unittest.main()
