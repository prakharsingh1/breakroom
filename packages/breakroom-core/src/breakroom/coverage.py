"""Executed negative-control evidence, separate from a customer release gate.

Manifest control names select entries in a maintained builtin registry. They are
never imported as contributor-provided module names, paths, or source code.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import importlib
import json
import math
from pathlib import Path
from typing import Callable

from .reports import validate_report, write_bundle
from .runner import run_case
from .scenarios import content_hash, validate_case

MATRIX_SCHEMA_VERSION = "1.0"
MAX_TRIALS = 500
CONTROL_NAMES = (
    "corrected", "faulty", "always_escalate", "success_without_evidence",
    "repeat_workflow", "first_name_only", "ignore_parameter_conflict",
    "suppress_same_order_refunds", "fresh_key_per_worker", "pending_as_succeeded",
    "ignore_terminal_failure", "ignore_retry_after", "unbounded_retry",
    "claim_despite_permission_denial", "assume_prior_permission",
    "overwrite_after_version_conflict", "clamp_to_balance", "force_order_currency",
    "fresh_key_per_delivery", "first_order_guess", "invent_missing_fields",
    "fresh_key_on_empty_lookup", "handle_event_twice", "trust_last_event_payload",
    "skip_ticket_ownership", "claim_unknown_success",
    "no_retry_after_transient",
)
CONTROL_REGISTRY = {name: f"breakroom.agents:{name}" for name in CONTROL_NAMES}


def _source_manifest_hash(report: dict) -> str:
    """Legacy reports freeze one manifest; varied reports also freeze its source."""
    case = report["case"]
    source_fields = {"source_manifest", "source_manifest_hash", "generator_version"}
    if source_fields & case.keys():
        if not source_fields <= case.keys():
            raise ValueError("Variation provenance is incomplete.")
        source = validate_case(case["source_manifest"])
        if source["case_id"] != case["case_id"] or content_hash(source) != case["source_manifest_hash"]:
            raise ValueError("Variation source manifest does not match its hash or case.")
        if not isinstance(case["generator_version"], str) or not 1 <= len(case["generator_version"]) <= 128:
            raise ValueError("Invalid variation generator version.")
        return case["source_manifest_hash"]
    return case["manifest_hash"]


def _coverage_complete(report: dict) -> bool:
    coverage = [check for check in report["checks"] if check["category"] == "coverage"]
    return bool(coverage) and all(check["status"] in {"pass", "not_applicable"} for check in coverage)


def classify_observation(report: dict, expectation: str | None) -> dict:
    """Classify independently validated evidence, never a control's display name."""
    validate_report(report)
    coverage_complete = _coverage_complete(report)
    failures = [check for check in report["checks"] if check["status"] == "fail"
                and check["category"] in {"safety", "outcome", "evidence", "liveness"}]
    liveness_failed = any(check["category"] == "liveness" for check in failures)
    observed_actions = any(event.get("kind") in {"tool_call", "invocation_started"}
                           or event.get("type") in {"tool_call", "invocation_started"}
                           for event in report["events"])
    execution = report["execution"]["status"]
    # A missing/import-broken adapter or worker crash is not a detected mutant.
    # Actual liveness violations remain useful when the runner terminates them.
    detection = (report["verdict"] == "FAIL" and coverage_complete and bool(failures)
                 and (execution == "completed" or (liveness_failed and observed_actions)))
    if expectation == "PASS":
        status = "control_pass" if report["verdict"] == "PASS" and coverage_complete else (
            "control_failed" if report["verdict"] == "FAIL" else "incomplete")
    elif expectation == "FAIL":
        status = "detected" if detection else ("missed" if report["verdict"] == "PASS" else "incomplete")
    else:
        status = "observed"
    return {
        "status": status,
        "observed_verdict": report["verdict"],
        "execution_status": execution,
        "coverage_complete": coverage_complete,
        "applicable_failed_checks": [check["id"] for check in failures],
        "observed_detection": detection,
    }


def summarize_cells(cells: list[dict], *, missing_negative_control_cases: list[str]) -> dict:
    expected = [cell for cell in cells if cell["expectation"] == "FAIL"]
    controls = [cell for cell in cells if cell["expectation"] == "PASS"]
    detected = sum(cell["status"] == "detected" for cell in expected)
    executed = sum(cell["report_index"] is not None for cell in cells)
    missed = sum(cell["status"] == "missed" for cell in expected)
    control_failures = sum(cell["status"] == "control_failed" for cell in controls)
    incomplete = sum(cell["status"] == "incomplete" for cell in cells if cell["expectation"] is not None)
    passing_controls = sum(cell["status"] == "control_pass" for cell in controls)
    gate = 1 if missed or control_failures else (
        2 if not expected or not controls or incomplete or missing_negative_control_cases
        or detected != len(expected) or passing_controls != len(controls) else 0)
    return {
        "executed_trial_count": executed,
        "expected_negative_trials": len(expected),
        "detected_negative_trials": detected,
        "missed_negative_trials": missed,
        "incomplete_negative_trials": sum(cell["status"] == "incomplete" for cell in expected),
        "expected_corrected_trials": len(controls),
        "passing_corrected_trials": passing_controls,
        "failed_corrected_trials": control_failures,
        "incomplete_corrected_trials": sum(cell["status"] == "incomplete" for cell in controls),
        "observed_verdict_counts": dict(Counter(cell["observed_verdict"] for cell in cells if cell["report_index"] is not None)),
        "detection_fraction": {"numerator": detected, "denominator": len(expected)},
        "not_scheduled_cells": sum(cell["status"] == "not_scheduled" for cell in cells),
        "missing_negative_control_cases": missing_negative_control_cases,
        "gate_exit": gate,
    }


def execute_matrix(cases: list[dict], *, seeds: tuple[int, ...] = (0,), trials: int = 1,
                   all_pairs: bool = False, timeout: float = 5.0,
                   runner: Callable = run_case) -> tuple[dict, list[dict]]:
    """Run corrected and declared mutants, retaining every expected denominator.

    Unmapped cells are explicitly not scheduled unless all_pairs is selected.
    A locally supplied runner callable is trusted test/integration code; manifests
    cannot provide it or expand CONTROL_REGISTRY.
    """
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128:
        raise ValueError("Coverage requires 1–128 validated cases.")
    cases = [validate_case(case) for case in cases]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Coverage case IDs must be unique.")
    if type(trials) is not int or not 1 <= trials <= 20:
        raise ValueError("Coverage trials must be an integer between 1 and 20.")
    if not isinstance(seeds, tuple) or not seeds or len(seeds) > 20:
        raise ValueError("Coverage requires 1–20 unique seed values.")
    if any(type(seed) is not int or not 0 <= seed <= 2**31 - 1 for seed in seeds):
        raise ValueError("Coverage seeds must be bounded non-negative integers.")
    if len(set(seeds)) != len(seeds):
        raise ValueError("Coverage seed values must be unique.")
    if type(all_pairs) is not bool:
        raise ValueError("all_pairs must be a boolean.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0.05 <= timeout <= 120:
        raise ValueError("Coverage timeout must be between 0.05 and 120 seconds.")
    controls = sorted({control for case in cases for control in case["negative_controls"]})
    if "corrected" in controls:
        raise ValueError("The corrected reference cannot also be a declared negative control.")
    if len(controls) > 64:
        raise ValueError("Coverage permits at most 64 distinct negative-control labels.")
    controls = ["corrected", *controls]
    for case in cases:
        if len(set(case["negative_controls"])) != len(case["negative_controls"]):
            raise ValueError("Duplicate negative-control mappings are invalid.")
        supported_seeds = case["variation_constraints"]["supported_seeds"]
        if any(seed not in supported_seeds for seed in seeds):
            raise ValueError(f"Selected seed is unsupported by {case['case_id']}.")
    scheduled = sum(len(controls) if all_pairs else 1 + len(case["negative_controls"]) for case in cases) * len(seeds) * trials
    if scheduled > MAX_TRIALS:
        raise ValueError(f"Coverage is bounded to {MAX_TRIALS} executions; select fewer cases, seeds, or repeats.")
    builtin_agents = importlib.import_module("breakroom.agents")
    cells, reports = [], []
    for case in cases:
        for seed in seeds:
            for trial_index in range(1, trials + 1):
                for control in controls:
                    expectation = "PASS" if control == "corrected" else ("FAIL" if control in case["negative_controls"] else None)
                    cell = {
                        "case_id": case["case_id"], "seed": seed, "trial_index": trial_index,
                        "control": control, "expectation": expectation,
                        "status": "not_scheduled", "observed_verdict": None,
                        "execution_status": None, "coverage_complete": None,
                        "applicable_failed_checks": [], "observed_detection": None,
                        "report_index": None, "run_id": None, "error": None,
                        "materialized_manifest_hash": None, "fixture_hash": None,
                        "source_manifest_hash": None, "generator_version": None,
                    }
                    if expectation is not None or all_pairs:
                        reference = CONTROL_REGISTRY.get(control)
                        if reference is None or not callable(getattr(builtin_agents, control, None)):
                            cell.update(status="incomplete", error="Control is not an available audited builtin; no code was imported from the manifest.")
                        else:
                            try:
                                report = runner(case, reference, seed=seed, timeout=timeout, allow_network=False, env_allowlist=())
                                validate_report(report)
                                if report["case"]["case_id"] != case["case_id"] or report["seed"] != seed or _source_manifest_hash(report) != content_hash(case) or report["agent"].get("reference") != reference:
                                    raise ValueError("Runner returned a different case, seed, or adapter identity.")
                                cell.update(classify_observation(report, expectation))
                                cell.update(report_index=len(reports), run_id=report["run_id"],
                                            materialized_manifest_hash=report["case"]["manifest_hash"],
                                            fixture_hash=report["case"]["fixture_hash"],
                                            source_manifest_hash=_source_manifest_hash(report),
                                            generator_version=report["case"].get("generator_version"))
                                reports.append(report)
                            except (ValueError, OSError, TypeError, KeyError, RuntimeError) as exc:
                                cell.update(status="incomplete", error=f"Runner could not produce valid trial evidence: {type(exc).__name__}.")
                    cells.append(cell)
    missing = [case["case_id"] for case in cases if not case["negative_controls"]]
    matrix = {
        "schema_version": MATRIX_SCHEMA_VERSION, "producer": "breakroom", "kind": "mutation_matrix",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "all_pairs" if all_pairs else "declared_pairs",
        "controls": controls, "seeds": list(seeds), "trials_per_pair": trials,
        "cases": [{"case_id": case["case_id"], "case_version": case["case_version"],
                   "pack_version": case["pack_version"], "manifest_hash": content_hash(case),
                   "negative_controls": list(case["negative_controls"])} for case in cases],
        "cells": cells, "summary": summarize_cells(cells, missing_negative_control_cases=missing),
        "evidence_bundle": "reports/report.json",
        "limitations": [
            "Synthetic scripted controls establish detection only under these modeled cases and seeds.",
            "Unknown, unsupported, untriggered, and missing expected observations remain in the denominator.",
            "Unmapped pairs are not scheduled by default; their behavior is not inferred.",
            "The mutation gate reviews the test pack. It is not a release gate for the faulty controls or a commercial-model benchmark.",
            "Reference runs use a minimal environment. A local Python subprocess is not a security sandbox.",
        ],
    }
    return matrix, reports


def write_matrix(matrix: dict, reports: list[dict], out: str | Path) -> Path:
    """Save the calculated matrix and actual versioned JSON/HTML/JUnit evidence."""
    case_map = {case["case_id"]: case for case in matrix["cases"]}
    if len(case_map) != len(matrix["cases"]) or not case_map or len(matrix["controls"]) != len(set(matrix["controls"])):
        raise ValueError("Invalid matrix case/control dimensions.")
    expected_cells = {(case_id, seed, trial, control) for case_id in case_map
                      for seed in matrix["seeds"] for trial in range(1, matrix["trials_per_pair"] + 1)
                      for control in matrix["controls"]}
    actual_cells = {(cell["case_id"], cell["seed"], cell["trial_index"], cell["control"]) for cell in matrix["cells"]}
    if actual_cells != expected_cells or len(actual_cells) != len(matrix["cells"]):
        raise ValueError("Matrix is missing, duplicating, or adding an expected cell.")
    missing = [case_id for case_id, case in case_map.items() if not case["negative_controls"]]
    expected = summarize_cells(matrix["cells"], missing_negative_control_cases=missing)
    if expected != matrix["summary"] or expected["executed_trial_count"] != len(reports):
        raise ValueError("Matrix summary contradicts its recorded evidence.")
    referenced = set()
    for cell in matrix["cells"]:
        expected_outcome = "PASS" if cell["control"] == "corrected" else ("FAIL" if cell["control"] in case_map[cell["case_id"]]["negative_controls"] else None)
        if cell["expectation"] != expected_outcome:
            raise ValueError("Matrix expectation contradicts the manifest mapping.")
        index = cell["report_index"]
        if index is not None:
            if type(index) is not int or not 0 <= index < len(reports):
                raise ValueError("Invalid matrix report reference.")
            if index in referenced:
                raise ValueError("The same recorded trial cannot stand in for independent repeats.")
            referenced.add(index)
            report = validate_report(reports[index])
            if report["run_id"] != cell["run_id"] or report["case"]["case_id"] != cell["case_id"] or report["seed"] != cell["seed"] or _source_manifest_hash(report) != case_map[cell["case_id"]]["manifest_hash"]:
                raise ValueError("Matrix cell references a different trial.")
            if cell["materialized_manifest_hash"] != report["case"]["manifest_hash"] or cell["fixture_hash"] != report["case"]["fixture_hash"] or cell["source_manifest_hash"] != _source_manifest_hash(report) or cell["generator_version"] != report["case"].get("generator_version"):
                raise ValueError("Matrix cell variation metadata disagrees with its recorded evidence.")
            if report["agent"].get("reference") != CONTROL_REGISTRY.get(cell["control"]):
                raise ValueError("Matrix cell references a different adapter.")
            if any(cell[key] != value for key, value in classify_observation(report, cell["expectation"]).items()):
                raise ValueError("Matrix cell contradicts the actual report.")
        elif cell["status"] not in {"incomplete", "not_scheduled"} or (expected_outcome is not None and cell["status"] == "not_scheduled"):
            raise ValueError("A cell without evidence cannot claim a completed observation.")
    payload = json.dumps(matrix, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(payload.encode()) > 4 * 1024 * 1024:
        raise ValueError("Mutation matrix exceeds 4 MiB.")
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    write_bundle(reports, destination / "reports", [case["case_id"] for case in matrix["cases"]])
    (destination / "matrix.json").write_text(payload, encoding="utf-8")
    summary = matrix["summary"]
    text = ["# Breakroom executed mutation evidence", "",
            f"Detected {summary['detected_negative_trials']} of {summary['expected_negative_trials']} declared negative trials.",
            f"Corrected control passed {summary['passing_corrected_trials']} of {summary['expected_corrected_trials']} required trials.",
            f"Executed {summary['executed_trial_count']} trials. Mutation gate exit: {summary['gate_exit']}.", "",
            "See `matrix.json` for every scheduled and unscheduled cell, and `reports/report.json` for the actual versioned trial evidence. The evidence bundle's ordinary release gate includes deliberate failures; the separate mutation gate checks whether the pack detects its declared mistakes.", "",
            *[f"- {limitation}" for limitation in matrix["limitations"]], ""]
    (destination / "README.md").write_text("\n".join(text), encoding="utf-8")
    return destination
