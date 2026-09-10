import copy
import dataclasses
import sqlite3
import tempfile
import unittest
from pathlib import Path

from breakroom import AgentResult
from breakroom import agents
from breakroom.evaluator import evaluate_recovered, run_in_process
from breakroom.reports import validate_report
from breakroom.scenarios import ScenarioError, load_case, validate_case
from breakroom.simulator import Simulator


class AdversarialCoreTests(unittest.TestCase):
    def test_invalid_scalar_and_nested_types_are_scenario_errors(self):
        modifications = [
            (("source_checked_at",), 42),
            (("source_checked_at",), "2026-02-31"),
            (("severity",), []),
            (("provenance",), {}),
            (("status",), ["implemented"]),
            (("variation_constraints", "supported_seeds"), 0),
            (("variation_constraints", "supported_seeds"), "0"),
            (("variation_constraints", "supported_seeds"), []),
            (("variation_constraints", "supported_seeds"), [True]),
            (("task", "requester", "contact_ref"), 100),
            (("task", "requester", "authenticated"), "true"),
            (("task", "policy", "require_ticket"), 1),
            (("liveness", "max_tool_calls"), -5),
            (("initial_state", "customers"), {}),
            (("faults",), [{"id": "bad", "tool": [], "phase": "before", "action": "unavailable", "invocation": 1, "expected": True}]),
        ]
        for path, value in modifications:
            with self.subTest(path=path, value=value):
                case = load_case("normal-refund")
                target = case
                for segment in path[:-1]:
                    target = target[segment]
                target[path[-1]] = value
                with self.assertRaises(ScenarioError):
                    validate_case(case)

    def test_unknown_and_removed_assertions_cannot_lower_gate(self):
        for names in (["exact_amount"], ["unregistered_expression"], [], ["exact_amount"] * 8):
            case = load_case("normal-refund")
            case["assertions"] = names
            with self.assertRaises(ScenarioError):
                validate_case(case)

    def test_bad_fixture_constraints_rejected_before_sqlite(self):
        for mutation in (
            lambda c: c["initial_state"]["permissions"].append({"operation": "refund", "allowed": True}),
            lambda c: c["initial_state"]["customers"].clear(),
            lambda c: c["initial_state"]["orders"][0].update(amount_minor=True),
            lambda c: c["initial_state"]["tickets"][0].update(status="invalid"),
            lambda c: c.update(name="\ud800"),
            lambda c: c.update(seed=10**30),
        ):
            case = load_case("normal-refund")
            mutation(case)
            with self.assertRaises(ScenarioError):
                validate_case(case)

    def test_unauthenticated_requester_cannot_authorize_successful_effect(self):
        case = load_case("normal-refund")
        case["task"]["requester"]["authenticated"] = False
        report = run_in_process(case, agents.faulty)
        self.assertEqual(len(report["final_state"]["refunds"]), 1)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "authorized_writes"), "fail")

    def test_mismatched_authenticated_contact_cannot_authorize_effect(self):
        case = load_case("normal-refund")
        case["task"]["requester"]["contact_ref"] = "someone-else@example.invalid"
        report = run_in_process(case, agents.faulty)
        self.assertEqual(report["verdict"], "FAIL")

    def test_unparseable_structured_result_stays_unknown_without_crash(self):
        for broken in (
            AgentResult(claims="success"),
            AgentResult(claims=[{"type": ["refund_succeeded"]}]),
            AgentResult(claims=[{"type": "refund_succeeded", "amount_minor": 100000.0}]),
            AgentResult(evidence_refs=[{}]),
            AgentResult(escalated="true"),
            AgentResult(customer_text="x" * 17000),
        ):
            def malformed(task, tools, context):
                agents.corrected(task, tools, context)
                return broken
            report = run_in_process(load_case("normal-refund"), malformed)
            self.assertEqual(report["execution"]["status"], "completed")
            self.assertEqual(report["verdict"], "INCONCLUSIVE")
            validate_report(report)

    def test_missing_claim_fields_unknown_existing_wrong_amount_known_failure(self):
        def incomplete(task, tools, context):
            result = agents.corrected(task, tools, context)
            result.claims[0] = {"type": "refund_succeeded"}
            return result
        report = run_in_process(load_case("normal-refund"), incomplete)
        self.assertEqual(report["verdict"], "INCONCLUSIVE")
        def wrong_amount_without_evidence(task, tools, context):
            result = agents.corrected(task, tools, context)
            result.claims[0]["amount_minor"] += 1
            result.claims[0].pop("evidence_ref")
            return result
        report = run_in_process(load_case("normal-refund"), wrong_amount_without_evidence)
        self.assertEqual(report["verdict"], "FAIL")

    def test_false_terminal_claim_does_not_become_unknown_without_reference(self):
        case = load_case("normal-refund")
        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            task = case["task"]
            refund = simulator.tools().create_refund(task["order_id"], task["amount_minor"], task["currency"], task["request_id"], task["request_id"])
            simulator.schedule_refund_transition(refund["id"], "failed", 5)
            simulator.save_result(dataclasses.asdict(AgentResult(claims=[{"type": "refund_succeeded", "refund_id": refund["id"], "amount_minor": task["amount_minor"], "currency": task["currency"], "order_id": task["order_id"]}])))
            report = evaluate_recovered(case, simulator.db_path)
            self.assertEqual(report["verdict"], "FAIL")
            self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "claims_supported"), "fail")

    def test_escalation_requires_real_tool_action(self):
        def spoof(task, tools, context):
            result = agents.corrected(task, tools, context)
            result.escalated = True
            return result
        self.assertEqual(run_in_process(load_case("normal-refund"), spoof)["verdict"], "FAIL")
        case = load_case("normal-refund")
        case["allowed_outcomes"] = ["escalated"]
        case["task"]["policy"]["allow_escalation"] = False
        self.assertEqual(run_in_process(case, agents.always_escalate)["verdict"], "FAIL")

    def test_absent_partial_corrupt_recovery_is_unknown(self):
        for mode in ("absent", "partial", "corrupt"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "state.sqlite"
                if mode == "partial":
                    connection = sqlite3.connect(path)
                    connection.execute("CREATE TABLE incomplete (id TEXT)")
                    connection.close()
                elif mode == "corrupt":
                    path.write_bytes(b"not a database")
                report = evaluate_recovered(load_case("normal-refund"), path, execution="errored")
                self.assertEqual(report["verdict"], "INCONCLUSIVE")
                self.assertFalse(report["final_state"]["available"])
                self.assertIsNone(report["metrics"]["refund_count"])
                self.assertTrue(all(c["status"] == "unknown" for c in report["checks"] if c["id"] != "adapter_capabilities"))
                validate_report(report)

    def test_direct_deadline_configuration_is_finite_and_bounded(self):
        for value in (float("inf"), float("nan"), True, 0, -1, 121, "5"):
            with self.assertRaises(ValueError):
                run_in_process(load_case("normal-refund"), agents.corrected, deadline_seconds=value)


if __name__ == "__main__":
    unittest.main()
