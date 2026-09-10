import copy
import json
import tempfile
import unittest
from pathlib import Path

from breakroom import AgentResult, ToolError
from breakroom import agents
from breakroom.evaluator import run_in_process
from breakroom.scenarios import ScenarioError, list_cases, load_case, load_pack, pack_directory, validate_case
from breakroom.simulator import Simulator


class CoreM2Tests(unittest.TestCase):
    def test_all_five_corrected_and_declared_negative_controls(self):
        cases = [case for case in list_cases() if case["number"] <= 5]
        self.assertEqual(len(cases), 5)
        for case in cases:
            with self.subTest(case=case["case_id"], agent="corrected"):
                self.assertEqual(run_in_process(case, agents.corrected)["verdict"], "PASS")
            for mutant in case["negative_controls"]:
                with self.subTest(case=case["case_id"], agent=mutant):
                    self.assertEqual(run_in_process(case, getattr(agents, mutant))["verdict"], "FAIL")

    def test_before_commit_failure_has_zero_effect_at_error(self):
        case = load_case("refund-before-commit")
        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            tools = simulator.tools()
            task = case["task"]
            with self.assertRaises(ToolError) as caught:
                tools.create_refund(task["order_id"], task["amount_minor"], task["currency"], task["request_id"], task["request_id"])
            self.assertEqual(caught.exception.code, "transient_error")
            self.assertEqual(simulator.snapshot()["refunds"], [])
            response = tools.create_refund(task["order_id"], task["amount_minor"], task["currency"], task["request_id"], task["request_id"])
            self.assertEqual(response["status"], "succeeded")
            self.assertEqual(len(simulator.snapshot()["refunds"]), 1)

    def test_ticket_retry_does_not_repeat_refund(self):
        case = load_case("ticket-write-failure")
        correct = run_in_process(case, agents.corrected)
        mutant = run_in_process(case, agents.repeat_workflow)
        self.assertEqual(correct["final_state"]["invocations"]["create_refund"], 1)
        self.assertEqual(correct["final_state"]["invocations"]["append_note"], 2)
        self.assertEqual(len(mutant["final_state"]["refunds"]), 2)
        self.assertEqual(mutant["verdict"], "FAIL")

    def test_customer_identity_is_independent_of_first_name_order(self):
        case = load_case("similar-customers")
        report = run_in_process(case, agents.first_name_only)
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["verdict"], "FAIL")
        refund = report["final_state"]["refunds"][0]
        self.assertEqual(refund["order_id"], "ord_000_distractor")
        self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "correct_target"), "fail")

    def test_safe_escalation_only_when_explicitly_permitted(self):
        case = load_case("normal-refund")
        case["allowed_outcomes"] = ["escalated"]
        case["task"]["policy"]["allow_escalation"] = True
        report = run_in_process(case, agents.always_escalate)
        self.assertEqual(report["verdict"], "PASS")

    def test_unsupported_required_capability_is_not_pass(self):
        case = load_case("normal-refund")
        case["required_capabilities"].append("event_stream")
        report = run_in_process(case, agents.corrected)
        self.assertEqual(report["verdict"], "UNSUPPORTED")

    def test_semantic_response_loss_evidence_cannot_prove_unseen_status(self):
        def wrong_evidence(task, tools, context):
            result = agents.corrected(task, tools, context)
            result.claims[0]["evidence_ref"] = result.claims[1]["evidence_ref"]
            return result
        report = run_in_process(load_case("normal-refund"), wrong_evidence)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "claims_supported"), "fail")

    def test_invalid_files_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "case.json"
            for body in ('{"case_id":"a","case_id":"b"}', '!!python/object/apply:os.system ["touch /tmp/never"]', '[' * 10000):
                path.write_text(body)
                with self.assertRaises(ScenarioError):
                    load_case(path)
            path.write_text(' ' * 262145)
            with self.assertRaises(ScenarioError):
                load_case(path)

    def test_cross_account_access_denied(self):
        case = load_case("similar-customers")
        case["initial_state"]["customers"][0]["tenant_id"] = "tenant_other"
        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            tools = simulator.tools()
            with self.assertRaises(ToolError):
                tools.get_order("ord_000_distractor")
            self.assertEqual(len(tools.find_customers("Anaya")["items"]), 1)

    def test_every_trial_has_independent_initial_state(self):
        case = load_case("refund-response-lost")
        first = run_in_process(case, agents.faulty)
        second = run_in_process(case, agents.corrected)
        self.assertEqual(first["initial_state"], second["initial_state"])
        self.assertEqual(len(second["final_state"]["refunds"]), 1)
        self.assertEqual(second["final_state"]["refunds"][0]["id"], "rf_000001")

    def test_packaged_manifests_match_reviewable_source(self):
        source = Path(__file__).resolve().parents[2] / "scenario-packs" / "support-refunds"
        for manifest in list_cases():
            self.assertEqual(manifest, load_case(source / (manifest["case_id"] + ".json")))


if __name__ == "__main__":
    unittest.main()
