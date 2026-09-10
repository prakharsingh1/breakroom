"""Deterministic, relationship-preserving variations of synthetic fixtures.

Version 1 varies identifiers, synthetic names/contacts, and integer money by a
common factor. It does not vary policies, fault schedules, currency, or the
authorization relationships which make a drill meaningful. This generator is
not an assertion of broader coverage than its reviewed seed set.
"""
from __future__ import annotations

import copy
import hashlib

from .scenarios import ScenarioError, validate_case

GENERATOR_VERSION = "1.0.0"
SUPPORTED_SEEDS = (0, 1, 2)
_DESCRIPTION = (
    f"Breakroom deterministic fixture generator {GENERATOR_VERSION}: reviewed seeds 0, 1, 2; "
    "consistent identifier/name/contact substitution and uniform integer money scaling."
)
_IDENTIFIER_FIELDS = frozenset({
    "id", "customer_id", "order_id", "ticket_id", "refund_id", "tenant_id",
    "request_id", "logical_request_id", "operation_key", "operation_id",
    "event_id", "consumer_operation_id",
})
_FIRST_NAMES = ("Mira", "Riya", "Tara", "Noor", "Isha", "Sana", "Leela", "Diya")


def _digest(seed: int, label: str) -> str:
    return hashlib.sha256(f"breakroom-fixtures:{GENERATOR_VERSION}:{seed}:{label}".encode()).hexdigest()


def materialize_case(case: dict, seed: int) -> dict:
    """Return a validated materialization without modifying the source manifest.

    Seed zero is an exact deep copy of the canonical manifest. A generated case
    can be passed through with its own seed, which keeps runner/export reruns
    idempotent. To choose a different seed, supply the canonical manifest again;
    a materialization does not contain enough data to reconstruct its source.
    Canonical-manifest and materialized-fixture hashes belong in report metadata,
    not additional fields in these strictly validated scenario manifests.
    """
    if type(seed) is not int or seed not in SUPPORTED_SEEDS:
        raise ScenarioError("seed is outside the generator's reviewed set: 0, 1, 2")
    if (case.get("variation_constraints", {}).get("description") == _DESCRIPTION
            and case.get("seed") != 0):
        if case["seed"] != seed:
            raise ScenarioError("choose a different variation from the canonical manifest, not an existing materialization")
        return validate_case(case)
    if seed == 0:
        return validate_case(case)

    materialized = copy.deepcopy(case)
    suffix = f"_s{seed}_{_digest(seed, 'identifiers')[:8]}"
    multiplier = 1 + int(_digest(seed, "money")[:8], 16) % 3
    identifiers: dict[str, str] = {}
    scopes = [materialized["initial_state"], materialized["task"]]
    if "execution_plan" in materialized:
        scopes.append(materialized["execution_plan"])

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in _IDENTIFIER_FIELDS and isinstance(child, str) and child != "system_edit":
                    # The same source value maps identically across every key:
                    # operation_key == request_id remains true when intended.
                    identifiers[child] = child + suffix
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for scope in scopes:
        collect(scope)

    def transform(value: object, field: str | None = None) -> object:
        if isinstance(value, dict):
            return {key: transform(child, key) for key, child in value.items()}
        if isinstance(value, list):
            return [transform(child, field) for child in value]
        if field == "amount_minor" and type(value) is int:
            return value * multiplier
        if isinstance(value, str):
            if value in identifiers:
                return identifiers[value]
            if field == "contact_ref":
                return f"person-{_digest(seed, 'contact:' + value)[:16]}@example.invalid"
            if field == "display_name":
                first, separator, remainder = value.partition(" ")
                replacement = _FIRST_NAMES[int(_digest(seed, "first-name:" + first)[:8], 16) % len(_FIRST_NAMES)]
                return replacement + (separator + remainder if separator else "")
        return value

    materialized["initial_state"] = transform(materialized["initial_state"])
    materialized["task"] = transform(materialized["task"])
    if "execution_plan" in materialized:
        materialized["execution_plan"] = transform(materialized["execution_plan"])
    tasks = [materialized["task"]]
    if "execution_plan" in materialized:
        tasks.extend(materialized["execution_plan"]["tasks"])
    for task in tasks:
        task["text"] = (
            f"Please refund {task['amount_minor']} minor units {task['currency']} "
            "for my request and update the associated support ticket. "
            "Use the supplied customer and order information to verify the request."
        )
    materialized["seed"] = seed
    materialized["variation_constraints"] = {
        "description": _DESCRIPTION, "supported_seeds": list(SUPPORTED_SEEDS),
    }
    return validate_case(materialized)
