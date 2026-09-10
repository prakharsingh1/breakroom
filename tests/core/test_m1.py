import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from breakroom import AgentResult, ToolError
from breakroom.agents import always_escalate, corrected, faulty, success_without_evidence
from breakroom.evaluator import evaluate_recovered, run_in_process
from breakroom.scenarios import ScenarioError, load_case, validate_case
from breakroom.simulator import Simulator


class CoreM1Tests(unittest.TestCase):
    def world(self, case_id="normal-refund"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        case = load_case(case_id)
        simulator = Simulator(case, Path(temporary.name) / "state.sqlite")
        return case, simulator, simulator.tools()

    def refund(self, tools, *, amount=100000, currency="INR", key="op-one", request="req-one", customer="cus_anaya"):
        return tools.create_refund("ord_1001", amount, currency, key, request, customer_id=customer)

    def test_flagship_real_effects_and_controls(self):
        normal = load_case("normal-refund")
        missing = load_case("refund-response-lost")
        for agent in (faulty, corrected):
            self.assertEqual(run_in_process(normal, agent)["verdict"], "PASS")
        baseline = run_in_process(missing, faulty)
        candidate = run_in_process(missing, corrected)
        self.assertEqual(baseline["verdict"], "FAIL")
        self.assertEqual(candidate["verdict"], "PASS")
        self.assertEqual(baseline["metrics"]["refunded_minor_by_currency"], {"INR": 200000})
        self.assertEqual(candidate["metrics"]["refunded_minor_by_currency"], {"INR": 100000})
        self.assertEqual(len({r["id"] for r in baseline["final_state"]["refunds"]}), 2)
        self.assertEqual(baseline["execution"]["status"], "completed")

    def test_agent_name_never_selects_verdict(self):
        def corrected(task, tools, context):
            return faulty(task, tools, context)
        report = run_in_process(load_case("refund-response-lost"), corrected)
        self.assertEqual(report["verdict"], "FAIL")

    def test_always_escalate_fails_recoverable_case(self):
        self.assertEqual(run_in_process(load_case("normal-refund"), always_escalate)["verdict"], "FAIL")

    def test_unknown_and_untriggered_never_pass(self):
        def no_claims(task, tools, context):
            corrected(task, tools, context)
            return AgentResult(customer_text="Finished")
        self.assertEqual(run_in_process(load_case("normal-refund"), no_claims)["verdict"], "INCONCLUSIVE")
        case = load_case("refund-response-lost")
        case["faults"][0]["invocation"] = 20
        report = run_in_process(case, corrected)
        self.assertEqual(report["verdict"], "INCONCLUSIVE")
        self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "fault_coverage"), "unknown")

    def test_false_claim_is_known_failure(self):
        report = run_in_process(load_case("normal-refund"), success_without_evidence)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["final_state"]["refunds"], [])

    def test_integer_minor_units_currency_balance_ownership(self):
        _, simulator, tools = self.world()
        for amount in (1.1, True, 0, -1):
            with self.assertRaises(ToolError) as caught:
                self.refund(tools, amount=amount)
            self.assertEqual(caught.exception.code, "invalid_amount")
        for kwargs, code in (({"currency": "USD"}, "currency_mismatch"), ({"amount": 500001}, "insufficient_refundable_balance"), ({"customer": "other"}, "ownership_mismatch")):
            with self.assertRaises(ToolError) as caught:
                self.refund(tools, **kwargs)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(simulator.snapshot()["refunds"], [])
        self.refund(tools, amount=450000)
        with self.assertRaises(ToolError):
            self.refund(tools, amount=100000, key="too-much", request="second")

    def test_same_key_atomic_and_changed_parameters_conflict(self):
        _, simulator, tools = self.world()
        first = self.refund(tools)
        second = self.refund(tools)
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(ToolError) as caught:
            self.refund(tools, amount=50000)
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(len(simulator.snapshot()["refunds"]), 1)

    def test_genuine_distinct_same_amount_requests_both_proceed(self):
        _, simulator, tools = self.world()
        first = self.refund(tools)
        second = self.refund(tools, key="op-two", request="req-two")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(sum(r["amount_minor"] for r in simulator.snapshot()["refunds"]), 200000)

    def test_real_overlapping_concurrency_is_atomic(self):
        _, simulator, tools = self.world()
        simulator.refund_barrier = threading.Barrier(8)
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda _: self.refund(tools), range(8)))
        self.assertEqual(len({r["id"] for r in responses}), 1)
        self.assertEqual(len(simulator.snapshot()["refunds"]), 1)
        self.assertEqual(simulator.snapshot()["invocations"]["create_refund"], 8)

    def test_response_loss_occurs_after_durable_commit(self):
        case, simulator, tools = self.world("refund-response-lost")
        with self.assertRaises(ToolError) as caught:
            self.refund(tools)
        self.assertEqual(caught.exception.code, "transport_timeout")
        self.assertEqual(len(simulator.snapshot()["refunds"]), 1)
        kinds = [event["kind"] for event in simulator.events()]
        self.assertLess(kinds.index("refund_committed"), kinds.index("fault_triggered"))
        self.assertNotIn("tool_response", kinds)
        recovered = evaluate_recovered(case, simulator.db_path)
        self.assertEqual(recovered["execution"]["status"], "timed_out")

    def test_duplicate_failure_survives_timeout_and_missing_evidence(self):
        case, simulator, tools = self.world()
        task = case["task"]
        for key in ("fresh1", "fresh2"):
            tools.create_refund(task["order_id"], task["amount_minor"], task["currency"], key, task["request_id"])
        report = evaluate_recovered(case, simulator.db_path)
        self.assertEqual(report["verdict"], "FAIL")

    def test_ticket_version_conflict_preserves_state(self):
        _, simulator, tools = self.world()
        tools.append_note("tkt_1001", "Concurrent user edit", "another-operation", 1)
        with self.assertRaises(ToolError) as caught:
            tools.update_ticket("tkt_1001", "resolved", "agent-operation", 1)
        self.assertEqual(caught.exception.code, "version_conflict")
        ticket = simulator.snapshot()["tickets"][0]
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(ticket["messages"][0]["text"], "Concurrent user edit")

    def test_pending_transition_uses_virtual_clock(self):
        _, simulator, tools = self.world()
        refund = self.refund(tools)
        simulator.schedule_refund_transition(refund["id"], "succeeded", 5)
        self.assertEqual(tools.get_refund(refund["id"])["status"], "pending")
        simulator.sleep(4)
        self.assertEqual(tools.get_refund(refund["id"])["status"], "pending")
        simulator.sleep(1)
        self.assertEqual(tools.get_refund(refund["id"])["status"], "succeeded")
        self.assertEqual(simulator.now(), 5)

    def test_tool_and_context_exclude_oracles(self):
        def inspect_contract(task, tools, context):
            for obj in (task, tools, context):
                for attr in ("case_id", "faults", "oracle", "db_path", "expected_answer"):
                    self.assertFalse(hasattr(obj, attr))
            return corrected(task, tools, context)
        self.assertEqual(run_in_process(load_case("normal-refund"), inspect_contract)["verdict"], "PASS")

    def test_strict_manifest_rejects_executable_or_invalid_data(self):
        for mutation in (
            lambda c: c.update(command="touch /tmp/unsafe"),
            lambda c: c["assertions"].append("__import__('os').system('echo no')"),
            lambda c: c["task"].update(amount_minor=1.1),
            lambda c: c["initial_state"]["orders"][0].update(currency="INR;DROP TABLE"),
            lambda c: c["faults"].append({"tool": "exec"}),
        ):
            case = copy.deepcopy(load_case("normal-refund"))
            mutation(case)
            with self.assertRaises(ScenarioError):
                validate_case(case)


if __name__ == "__main__":
    unittest.main()
