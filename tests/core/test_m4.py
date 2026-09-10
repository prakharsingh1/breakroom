import copy
import tempfile
import unittest
from pathlib import Path

from breakroom import AgentResult, ToolError, agents
from breakroom.evaluator import evaluate_recovered, run_in_process
from breakroom.reports import compare_reports, validate_report
from breakroom.runner import run_case
from breakroom.scenarios import ScenarioError, list_cases, load_case, validate_case
from breakroom.simulator import Simulator


class M4MechanismTests(unittest.TestCase):
    def test_all_24_controls_and_actual_declared_detection(self):
        cases = list_cases()
        self.assertEqual({case["number"] for case in cases}, set(range(1,25)))
        for case in cases:
            with self.subTest(case=case["case_id"], agent="corrected"):
                report = run_in_process(case, agents.corrected)
                self.assertEqual(report["verdict"], "PASS", report["checks"])
                validate_report(report)
            for name in case["negative_controls"]:
                with self.subTest(case=case["case_id"], agent=name):
                    report = run_in_process(case, getattr(agents, name))
                    self.assertEqual(report["verdict"], "FAIL", report["checks"])
                    self.assertTrue(all(c["status"] == "pass" for c in report["checks"] if c["id"] in {"fault_coverage", "behavior_coverage", "adapter_capabilities"}))
                    validate_report(report)

    def test_distinct_versus_duplicate_requests_are_different_authorizations(self):
        distinct = run_in_process(load_case("distinct-same-amount-refunds"), agents.corrected)
        duplicate = run_in_process(load_case("duplicate-request"), agents.corrected)
        self.assertEqual(len(distinct["final_state"]["refunds"]), 2)
        self.assertEqual(len(duplicate["final_state"]["refunds"]), 1)
        self.assertEqual(len(distinct["final_state"]["tickets"][0]["messages"]), 2)
        self.assertEqual(len(duplicate["final_state"]["tickets"][0]["messages"]), 1)
        self.assertEqual(len(distinct["agent_result"]["invocations"]), 2)

    def test_concurrent_handlers_actually_overlap_and_commit_once(self):
        case = load_case("concurrent-same-request")
        for _ in range(5):
            report = run_in_process(case, agents.corrected)
            arrivals = [e for e in report["events"] if e["kind"] == "barrier_arrived"]
            first_commit = next(e["seq"] for e in report["events"] if e["kind"] == "refund_committed")
            self.assertEqual(len({e["data"]["handler_id"] for e in arrivals}), 2)
            self.assertTrue(all(e["seq"] < first_commit for e in arrivals))
            self.assertEqual(report["verdict"], "PASS")
            self.assertEqual(len(report["final_state"]["refunds"]), 1)

    def test_pending_creation_is_never_recorded_as_initial_success(self):
        report = run_in_process(load_case("pending-refund-succeeds"), agents.corrected)
        committed = next(e for e in report["events"] if e["kind"] == "refund_committed")
        transition = next(e for e in report["events"] if e["kind"] == "refund_transition")
        self.assertEqual(committed["data"]["refund"]["status"], "pending")
        self.assertEqual(transition["data"]["status"], "succeeded")
        self.assertLess(committed["at"], transition["at"])

    def test_eventual_success_cannot_retroactively_support_pending_claim(self):
        def lies_then_waits(task, tools, context):
            refund = tools.create_refund(task.order_id, task.amount_minor, task.currency, context.logical_operation_id, task.request_id)
            result = agents._finish(task, tools, context, refund, retry_ticket=True)
            context.sleep(3)
            return result
        report = run_in_process(load_case("pending-refund-succeeds"), lies_then_waits)
        self.assertEqual(report["final_state"]["refunds"][0]["status"], "succeeded")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "claims_supported"), "fail")

    def test_later_handler_cannot_retroactively_validate_earlier_false_claim(self):
        case = load_case("pending-refund-succeeds")
        case["execution_plan"]["mode"] = "sequential"
        case["execution_plan"]["tasks"].append(copy.deepcopy(case["task"]))
        def claim_then_later_reconcile(task, tools, context):
            found = tools.list_refunds(task.order_id, task.request_id)
            if not found["items"]:
                return agents.pending_as_succeeded(task, tools, context)
            context.sleep(3)
            return agents.corrected(task, tools, context)
        report = run_in_process(case, claim_then_later_reconcile)
        self.assertEqual(report["final_state"]["refunds"][0]["status"], "succeeded")
        self.assertEqual(report["verdict"], "FAIL")

    def test_pending_failure_releases_reserved_balance(self):
        case = load_case("pending-refund-fails")
        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            tools = simulator.tools()
            task = case["task"]
            tools.create_refund(task["order_id"], task["amount_minor"], task["currency"], task["request_id"], task["request_id"])
            self.assertEqual(tools.get_order(task["order_id"])["remaining_minor"], 400000)
            simulator.sleep(2)
            self.assertEqual(tools.get_order(task["order_id"])["remaining_minor"], 500000)

    def test_rate_limit_refuses_each_early_retry(self):
        case = load_case("temporary-rate-limit")
        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            tools, task = simulator.tools(), case["task"]
            for when in (0,1,2):
                if when:
                    simulator.sleep(1)
                with self.assertRaises(ToolError) as caught:
                    tools.create_refund(task["order_id"], task["amount_minor"], task["currency"], task["request_id"], task["request_id"])
                self.assertEqual(caught.exception.code, "rate_limit")
            simulator.sleep(1)
            self.assertEqual(tools.create_refund(task["order_id"], task["amount_minor"], task["currency"], task["request_id"], task["request_id"])["status"], "succeeded")
            self.assertEqual(len(simulator.snapshot()["refunds"]), 1)

    def test_permission_revocation_is_between_read_and_refused_write(self):
        report = run_in_process(load_case("permission-revoked-after-ticket-read"), agents.corrected)
        revoke = next(e["seq"] for e in report["events"] if e["kind"] == "permission_revoked")
        read = next(e["seq"] for e in report["events"] if e["kind"] == "tool_call" and e["tool"] == "get_ticket")
        denied = next(e["seq"] for e in report["events"] if e["kind"] == "tool_error" and e["data"]["code"] == "permission_denied")
        self.assertLess(read, revoke)
        self.assertLess(revoke, denied)
        self.assertEqual(report["final_state"]["refunds"], [])

    def test_real_concurrent_edit_is_preserved(self):
        report = run_in_process(load_case("ticket-version-conflict"), agents.corrected)
        self.assertEqual(report["verdict"], "PASS")
        ticket = report["final_state"]["tickets"][0]
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(ticket["messages"][0]["operation_id"], "system_edit")
        self.assertTrue(any(e["kind"] == "tool_error" and e["data"]["code"] == "version_conflict" for e in report["events"]))

    def test_stale_and_permanently_unknown_windows_are_distinct(self):
        stale = run_in_process(load_case("stale-refund-read"), agents.corrected)
        unknown = run_in_process(load_case("unresolved-before-deadline"), agents.corrected)
        self.assertEqual(stale["verdict"], "PASS")
        self.assertFalse(stale["agent_result"]["escalated"])
        self.assertTrue(any(e["kind"] == "stale_read" for e in stale["events"]))
        self.assertEqual(unknown["verdict"], "PASS")
        self.assertTrue(unknown["agent_result"]["escalated"])
        self.assertTrue(all(c["type"] != "refund_succeeded" for c in unknown["agent_result"]["claims"]))
        self.assertEqual(len(unknown["final_state"]["refunds"]), 1)

    def test_duplicate_events_are_received_twice_but_applied_once(self):
        report = run_in_process(load_case("duplicate-event-delivery"), agents.corrected)
        received = [e["data"]["event"] for e in report["events"] if e["kind"] == "event_received"]
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0]["event_id"], received[1]["event_id"])
        self.assertNotEqual(received[0]["delivery_id"], received[1]["delivery_id"])
        self.assertEqual(len(report["final_state"]["tickets"][0]["messages"]), 1)
        self.assertTrue(all(d["acked"] for d in report["final_state"]["event_deliveries"]))

    def test_reordering_is_observed_and_authoritative_state_reconciled(self):
        case = load_case("reordered-events")
        corrected = run_in_process(case, agents.corrected)
        bad = run_in_process(case, agents.trust_last_event_payload)
        received = [e["data"]["event"] for e in corrected["events"] if e["kind"] == "event_received"]
        self.assertEqual([e["status"] for e in received], ["succeeded", "pending"])
        self.assertGreater(received[0]["created_at"], received[1]["created_at"])
        self.assertEqual(corrected["final_state"]["tickets"][0]["status"], "resolved")
        self.assertEqual(bad["final_state"]["tickets"][0]["status"], "open")

    def test_missing_event_capability_is_unsupported_not_false_fail_or_pass(self):
        def ordinary_adapter(task, tools, context):
            return agents.corrected(task, tools, context)
        report = run_in_process(load_case("duplicate-event-delivery"), ordinary_adapter)
        self.assertEqual(report["verdict"], "UNSUPPORTED")
        self.assertEqual(report["final_state"]["refunds"], [])

    def test_new_schema_is_bounded_and_legacy_manifest_stays_supported(self):
        for mutation in (
            lambda c: c["execution_plan"].update(mode="eval"),
            lambda c: c["execution_plan"].update(tasks=[c["task"]]*9),
            lambda c: c["behavior"].update(command="rm -rf"),
            lambda c: c["behavior"].update(transition_delay=-1),
            lambda c: c["expectations"].update(observations=["unregistered_expression"]),
        ):
            case = load_case("pending-refund-succeeds")
            mutation(case)
            with self.assertRaises(ScenarioError):
                validate_case(case)
        self.assertEqual(load_case("normal-refund")["schema_version"], "1.0")

    def test_new_versions_do_not_compare_as_old_oracle(self):
        report = run_in_process(load_case("normal-refund"), agents.corrected)
        prior = copy.deepcopy(report)
        prior["oracle_version"] = "1.0"
        comparison = compare_reports([prior], [report])
        self.assertFalse(comparison["pairs"][0]["compatible"])

    def test_grouped_and_event_trials_survive_actual_subprocess_report_validation(self):
        for slug in ("distinct-same-amount-refunds", "concurrent-same-request", "pending-refund-fails", "duplicate-event-delivery", "reordered-events"):
            report = run_case(load_case(slug), "breakroom.agents:corrected")
            self.assertEqual(report["verdict"], "PASS", (slug, report["execution"], report["checks"]))
            self.assertEqual(report["oracle_version"], "2.0")
            validate_report(report)


if __name__ == "__main__":
    unittest.main()
