import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from breakroom.models import AgentCancelled, AgentResult, BudgetExceeded
from breakroom.execution import execute_invocations
from breakroom.scenarios import load_case
from breakroom.simulator import Simulator


class InvocationExecutionTests(unittest.TestCase):
    def make_case(self, mode="sequential", *, same_request=False):
        case = load_case("normal-refund")
        first = copy.deepcopy(case["task"])
        second = copy.deepcopy(first)
        if not same_request:
            second["request_id"] = "req_independently_authorized"
        case["schema_version"] = "2.0"
        case["execution_plan"] = {"mode": mode, "tasks": [first] if mode == "single" else [first, second]}
        return case

    @staticmethod
    def refund(task, tools, context):
        response = tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                       context.logical_operation_id, task.request_id,
                                       customer_id=task.customer_id)
        return AgentResult(claims=[{"type": "refund_succeeded", "refund_id": response["id"],
                                    "evidence_ref": response["evidence_ref"]}],
                           evidence_refs=[response["evidence_ref"]])

    def execute(self, case, agent, *, deadline_seconds=5, cancel_signal=None):
        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            with patch.object(simulator, "save_invocation_result", wraps=simulator.save_invocation_result) as saved:
                records, execution = execute_invocations(case, agent, simulator,
                    deadline_seconds=deadline_seconds, cancel_signal=cancel_signal)
                self.assertEqual(saved.call_count, len(case["execution_plan"]["tasks"]))
                for call in saved.call_args_list:
                    self.assertIn(call.args[1], records)
                    self.assertEqual(call.args[0], call.args[1]["handler_id"])
            recovered = Simulator(case, simulator.db_path, initialize=False)
            self.assertEqual(recovered.recovered_invocation_results(), records)
            self.assertIsNone(simulator.context)
            return records, execution, simulator.snapshot(), simulator.events()

    def test_distinct_sequential_requests_produce_independent_effects(self):
        case = self.make_case()
        records, execution, state, events = self.execute(case, self.refund)
        self.assertEqual(execution["status"], "completed")
        self.assertEqual([record["handler_id"] for record in records], ["handler_1", "handler_2"])
        self.assertEqual({refund["logical_request_id"] for refund in state["refunds"]},
                         {task["request_id"] for task in case["execution_plan"]["tasks"]})
        self.assertEqual(len(state["refunds"]), 2)
        boundaries = [event for event in events if event["kind"].startswith("invocation_")]
        self.assertEqual([event["kind"] for event in boundaries],
                         ["invocation_started", "invocation_returned"] * 2)
        self.assertEqual(records[0]["last_event_seq"], boundaries[1]["seq"])
        self.assertLess(records[0]["last_event_seq"], boundaries[2]["seq"])

    def test_duplicate_sequential_delivery_reuses_committed_effect(self):
        records, execution, state, events = self.execute(self.make_case(same_request=True), self.refund)
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(len(state["refunds"]), 1)
        self.assertEqual(records[0]["result"]["claims"][0]["refund_id"],
                         records[1]["result"]["claims"][0]["refund_id"])
        self.assertNotEqual(records[0]["result"]["evidence_refs"], records[1]["result"]["evidence_refs"])

    def test_concurrent_duplicate_handlers_overlap_and_use_distinct_contexts(self):
        entered = threading.Barrier(2)
        observations = []
        guard = threading.Lock()

        def concurrent(task, tools, context):
            with guard:
                observations.append((threading.get_ident(), context, tools))
            entered.wait(timeout=2)
            return self.refund(task, tools, context)

        records, execution, state, events = self.execute(self.make_case("concurrent", same_request=True), concurrent)
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(len(state["refunds"]), 1)
        self.assertEqual(len({observation[0] for observation in observations}), 2)
        self.assertIsNot(observations[0][1], observations[1][1])
        self.assertIsNot(observations[0][2], observations[1][2])
        self.assertEqual(observations[0][1]._wall_deadline, observations[1][1]._wall_deadline)
        starts = [event["seq"] for event in events if event["kind"] == "invocation_started"]
        returns = [event["seq"] for event in events if event["kind"] == "invocation_returned"]
        self.assertLess(max(starts), min(returns))
        arrivals = [event for event in events if event["kind"] == "barrier_arrived"]
        self.assertEqual({event["data"]["handler_id"] for event in arrivals}, {"handler_1", "handler_2"})
        self.assertLess(max(event["seq"] for event in arrivals),
                        min(event["seq"] for event in events if event["kind"] == "refund_committed"))
        self.assertEqual(len(records), 2)

    def test_concurrent_tool_evidence_is_attributed_to_the_observing_handler(self):
        records, execution, state, events = self.execute(self.make_case("concurrent"), self.refund)
        by_id = {event["id"]: event for event in events}
        starts = {event["data"]["handler_id"]: event["seq"] for event in events
                  if event["kind"] == "invocation_started"}
        for record in records:
            for ref in record["result"]["evidence_refs"]:
                event = by_id[ref]
                self.assertEqual(event["kind"], "tool_response")
                self.assertEqual(event["data"]["handler_id"], record["handler_id"])
                self.assertEqual(event["data"]["request_id"], record["request_id"])
                self.assertGreater(event["seq"], starts[record["handler_id"]])
                self.assertLess(event["seq"], record["last_event_seq"])

    def test_completed_handler_is_durable_before_other_handler_returns(self):
        case = self.make_case("concurrent")
        completed_persisted = threading.Event()
        release_pending = threading.Event()

        def partly_blocked(task, tools, context):
            result = self.refund(task, tools, context)
            if task.request_id == "req_independently_authorized":
                if not release_pending.wait(timeout=2):
                    raise RuntimeError("Test did not release pending handler")
            return result

        with tempfile.TemporaryDirectory() as temporary:
            simulator = Simulator(case, Path(temporary) / "state.sqlite")
            save = simulator.save_invocation_result

            def persist(handler_id, record):
                save(handler_id, record)
                if handler_id == "handler_1":
                    completed_persisted.set()

            with patch.object(simulator, "save_invocation_result", side_effect=persist):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(execute_invocations, case, partly_blocked,
                                             simulator, deadline_seconds=5)
                    try:
                        self.assertTrue(completed_persisted.wait(timeout=2))
                        recovered = Simulator(case, simulator.db_path, initialize=False)
                        records = recovered.recovered_invocation_results()
                        self.assertEqual([record["handler_id"] for record in records], ["handler_1"])
                        self.assertEqual(records[0]["execution"]["status"], "completed")
                        self.assertIsNotNone(records[0]["result"])
                        self.assertFalse(future.done())
                    finally:
                        release_pending.set()
                    records, execution = future.result(timeout=2)
            self.assertEqual(execution["status"], "completed")
            self.assertEqual(simulator.recovered_invocation_results(), records)

    def test_concurrent_distinct_requests_do_not_deduplicate_equal_amounts(self):
        records, execution, state, events = self.execute(self.make_case("concurrent"), self.refund)
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(len(state["refunds"]), 2)
        self.assertEqual(len({row["logical_request_id"] for row in state["refunds"]}), 2)

    def test_retry_after_first_concurrent_create_does_not_wait_at_barrier_again(self):
        def repeat(task, tools, context):
            self.refund(task, tools, context)
            if task.request_id == "req_independently_authorized":
                return self.refund(task, tools, context)
            return AgentResult()

        records, execution, state, events = self.execute(self.make_case("concurrent"), repeat)
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(state["invocations"]["create_refund"], 3)
        self.assertEqual(len(state["refunds"]), 2)

    def test_one_failure_after_concurrent_commit_retains_other_result_and_effect(self):
        def failing(task, tools, context):
            result = self.refund(task, tools, context)
            if task.request_id == "req_independently_authorized":
                raise RuntimeError("Failure after commit")
            return result

        records, execution, state, events = self.execute(self.make_case("concurrent"), failing)
        self.assertEqual(execution["status"], "errored")
        self.assertIn("handler_2: RuntimeError: Failure after commit", execution["error"])
        self.assertEqual(records[0]["execution"]["status"], "completed")
        self.assertIsNotNone(records[0]["result"])
        self.assertIsNone(records[1]["result"])
        self.assertEqual(len(state["refunds"]), 2)

    def test_unparseable_and_malformed_returns_remain_completed_with_unknown_evidence(self):
        for returned in ({"claims": []}, None, AgentResult(claims=[{"amount_minor": True}]),
                         AgentResult(evidence_refs="evt_000001"), AgentResult(claims=[{"nested": {}}])):
            with self.subTest(returned=returned):
                records, execution, state, events = self.execute(self.make_case("single"),
                                                                 lambda task, tools, context: returned)
                self.assertEqual(execution["status"], "completed")
                self.assertEqual(records[0]["execution"]["status"], "completed")
                self.assertIsNone(records[0]["result"])
                self.assertIn("unknown", execution["error"])

    def test_exception_types_aggregate_without_losing_handler_statuses(self):
        def failing(task, tools, context):
            if task.request_id == "req_independently_authorized":
                raise AgentCancelled("Stop requested")
            raise BudgetExceeded("No budget remains")

        records, execution, state, events = self.execute(self.make_case(), failing)
        self.assertEqual([record["execution"]["status"] for record in records], ["timed_out", "cancelled"])
        self.assertEqual(execution["status"], "cancelled")
        self.assertIn("No budget remains", execution["error"])
        self.assertIn("Stop requested", execution["error"])

    def test_cancelled_plan_records_all_deliveries_without_entering_adapter(self):
        cancelled = threading.Event()
        cancelled.set()
        entered = []
        records, execution, state, events = self.execute(self.make_case(),
            lambda task, tools, context: entered.append(task), cancel_signal=cancelled)
        self.assertEqual(entered, [])
        self.assertEqual(execution["status"], "cancelled")
        self.assertTrue(all(record["execution"]["status"] == "cancelled" for record in records))

    def test_all_sequential_deliveries_share_one_wall_deadline(self):
        contexts = []
        clock = [100.0]

        def slow(task, tools, context):
            contexts.append(context)
            clock[0] += 0.06
            return AgentResult()

        with patch("breakroom.execution.time.monotonic", side_effect=lambda: clock[0]):
            records, execution, state, events = self.execute(self.make_case(), slow, deadline_seconds=0.03)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(execution["status"], "timed_out")
        self.assertEqual([record["execution"]["status"] for record in records], ["timed_out", "timed_out"])

    def test_adapter_mutation_does_not_change_manifest_or_later_delivery(self):
        case = self.make_case(same_request=True)
        original = copy.deepcopy(case)
        observed = []

        def mutate(task, tools, context):
            observed.append(task.policy["refund_authorized"])
            task.policy["refund_authorized"] = False
            self.assertEqual(context.logical_operation_id, task.request_id)
            self.assertFalse(hasattr(task, "case_id"))
            self.assertFalse(hasattr(context, "execution_plan"))
            return AgentResult()

        self.execute(case, mutate)
        self.assertEqual(observed, [True, True])
        self.assertEqual(case, original)


if __name__ == "__main__":
    unittest.main()
