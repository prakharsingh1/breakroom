import copy
import unittest

from breakroom import agents
from breakroom.evaluator import run_in_process
from breakroom.scenarios import ScenarioError, content_hash, list_cases, load_case, validate_case
from breakroom.variations import GENERATOR_VERSION, SUPPORTED_SEEDS, materialize_case


class DeterministicVariationTests(unittest.TestCase):
    def test_seed_zero_is_an_independent_identity_copy(self):
        case = load_case("normal-refund")
        variant = materialize_case(case, 0)
        self.assertEqual(variant, case)
        self.assertIsNot(variant, case)
        variant["task"]["requester"]["display_name"] = "A different person"
        self.assertNotEqual(variant, case)

    def test_all_48_noncanonical_variations_validate_without_mutating_sources(self):
        cases = list_cases()
        self.assertEqual(len(cases), 24)
        for case in cases:
            original = copy.deepcopy(case)
            hashes = set()
            for seed in SUPPORTED_SEEDS[1:]:
                with self.subTest(case=case["case_id"], seed=seed):
                    variant = materialize_case(case, seed)
                    self.assertEqual(validate_case(variant), variant)
                    self.assertEqual(materialize_case(case, seed), variant)
                    self.assertEqual(materialize_case(variant, seed), variant)
                    self.assertEqual(variant["seed"], seed)
                    self.assertEqual(variant["variation_constraints"]["supported_seeds"], list(SUPPORTED_SEEDS))
                    self.assertIn(GENERATOR_VERSION, variant["variation_constraints"]["description"])
                    hashes.add(content_hash(variant))
            self.assertEqual(len(hashes), 2)
            self.assertEqual(case, original)

    def test_amount_scaling_preserves_every_fixture_and_request_ratio(self):
        for case in list_cases():
            for seed in SUPPORTED_SEEDS[1:]:
                with self.subTest(case=case["case_id"], seed=seed):
                    variant = materialize_case(case, seed)
                    multiplier = variant["task"]["amount_minor"] // case["task"]["amount_minor"]
                    self.assertIn(multiplier, (1, 2, 3))
                    pairs = [(case["task"], variant["task"])]
                    for collection in ("orders", "refunds"):
                        pairs.extend(zip(case["initial_state"][collection], variant["initial_state"][collection]))
                    if "execution_plan" in case:
                        pairs.extend(zip(case["execution_plan"]["tasks"], variant["execution_plan"]["tasks"]))
                    for original, transformed in pairs:
                        self.assertEqual(transformed["amount_minor"], original["amount_minor"] * multiplier)
                        self.assertIs(type(transformed["amount_minor"]), int)
                        self.assertEqual(transformed["currency"], original["currency"])
                    self.assertEqual(variant["faults"], case["faults"])
                    self.assertEqual(variant["liveness"], case["liveness"])
                    self.assertEqual(variant.get("behavior"), case.get("behavior"))

    def test_fixture_task_and_requester_foreign_keys_remain_consistent(self):
        for case in list_cases():
            variant = materialize_case(case, 1)
            customers = {row["id"]: row for row in variant["initial_state"]["customers"]}
            orders = {row["id"]: row for row in variant["initial_state"]["orders"]}
            tickets = {row["id"]: row for row in variant["initial_state"]["tickets"]}
            tasks = variant.get("execution_plan", {"tasks": [variant["task"]]})["tasks"]
            for task in tasks:
                requester = task["requester"]
                customer = customers[requester["customer_id"]]
                self.assertEqual(customer["tenant_id"], requester["tenant_id"])
                self.assertEqual(customer["contact_ref"], requester["contact_ref"])
                self.assertEqual(customer["display_name"], requester["display_name"])
                self.assertTrue(customer["contact_ref"].endswith(".invalid"))
                if task["order_id"]:
                    self.assertIn(task["order_id"], orders)
                if task["ticket_id"]:
                    self.assertIn(task["ticket_id"], tickets)
                self.assertIn(str(task["amount_minor"]), task["text"])
                self.assertIn(task["currency"], task["text"])
                self.assertNotIn(case["case_id"], task["text"])

    def test_parameter_conflict_preserves_existing_key_and_logical_request_identity(self):
        for seed in SUPPORTED_SEEDS[1:]:
            variant = materialize_case(load_case("idempotency-parameter-conflict"), seed)
            existing = variant["initial_state"]["refunds"][0]
            self.assertEqual(existing["operation_key"], variant["task"]["request_id"])
            self.assertEqual(existing["logical_request_id"], variant["task"]["request_id"])
            self.assertEqual(existing["order_id"], variant["task"]["order_id"])
            self.assertNotEqual(existing["amount_minor"], variant["task"]["amount_minor"])

    def test_duplicate_and_distinct_deliveries_keep_their_authorization_identity(self):
        for slug in ("duplicate-request", "concurrent-same-request", "distinct-same-amount-refunds"):
            variant = materialize_case(load_case(slug), 2)
            tasks = variant["execution_plan"]["tasks"]
            self.assertEqual(variant["task"], tasks[0])
            self.assertEqual(tasks[0]["amount_minor"], tasks[1]["amount_minor"])
            self.assertEqual(tasks[0]["request_id"] == tasks[1]["request_id"], slug != "distinct-same-amount-refunds")

    def test_shared_first_name_and_distractor_ordering_are_preserved(self):
        for seed in SUPPORTED_SEEDS[1:]:
            variant = materialize_case(load_case("similar-customers"), seed)
            customers = sorted(variant["initial_state"]["customers"], key=lambda row: row["id"])
            requester = variant["task"]["requester"]
            self.assertEqual(len({row["display_name"].split()[0] for row in customers}), 1)
            self.assertNotEqual(customers[0]["id"], requester["customer_id"])
            self.assertEqual(customers[1]["id"], requester["customer_id"])
            self.assertNotEqual(customers[0]["contact_ref"], requester["contact_ref"])

    def test_unknown_or_invalid_seeds_are_rejected(self):
        case = load_case("normal-refund")
        for seed in (-1, 3, 7, 42, 100, True, False, 1.0, "1", None):
            with self.subTest(seed=seed), self.assertRaises(ScenarioError):
                materialize_case(case, seed)

    def test_materialized_input_cannot_silently_be_scaled_again_for_another_seed(self):
        variant = materialize_case(load_case("normal-refund"), 1)
        for seed in (0, 2):
            with self.subTest(seed=seed), self.assertRaisesRegex(ScenarioError, "canonical manifest"):
                materialize_case(variant, seed)

    def test_all_variant_corrected_controls_pass_and_declared_mutants_fail(self):
        for case in list_cases():
            for seed in SUPPORTED_SEEDS[1:]:
                variant = materialize_case(case, seed)
                with self.subTest(case=case["case_id"], seed=seed, agent="corrected"):
                    report = run_in_process(variant, agents.corrected, seed=seed)
                    self.assertEqual(report["verdict"], "PASS", report["checks"])
                for mutant in case["negative_controls"]:
                    with self.subTest(case=case["case_id"], seed=seed, agent=mutant):
                        report = run_in_process(variant, getattr(agents, mutant), seed=seed)
                        self.assertEqual(report["verdict"], "FAIL", report["checks"])


if __name__ == "__main__":
    unittest.main()
