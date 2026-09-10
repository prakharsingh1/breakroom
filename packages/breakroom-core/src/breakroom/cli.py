"""Breakroom local CLI. Only an explicit upload-report command sends a report."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile

from . import __version__
from .reports import compare_reports, export_case, gate_exit, load_bundle, write_bundle
from .runner import run_case, validate_agent_reference
from .scenarios import list_cases, load_case, load_pack


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="breakroom", description="Breakroom — Crash tests for AI agents. Local simulated services; trusted adapter code.")
    root.add_argument("--version", action="version", version=f"breakroom {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local prerequisites without installing or uploading anything")
    demo = commands.add_parser("demo", help="Run the scripted faulty/corrected lost-response tutorial")
    demo.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    run = commands.add_parser("run", help="Execute a local trusted module:function adapter against a Fire Drill pack")
    run.add_argument("--agent", required=True)
    run.add_argument("--pack", default="support-refunds", help="Built-in support-refunds or a local manifest directory")
    run.add_argument("--case", action="append", dest="cases", help="Select case ID; repeat for multiple cases")
    run.add_argument("--out", type=Path, default=Path("artifacts/run"))
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--trials", type=int, default=1, help="1–20 independent trials per case at the selected fixture seed")
    run.add_argument("--timeout", type=float, default=5.0, help="Independent wall deadline, 0.05–120 seconds")
    run.add_argument("--allow-network", action="store_true", help="Explicitly opt into provider configuration for trusted local code; subprocesses do not block network")
    run.add_argument("--env", action="append", default=[], help="Named provider variable to pass (requires --allow-network); never pass payment/help-desk credentials")
    compare = commands.add_parser("compare", help="Compare recorded compatible case/seed pairs; does not rerun")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--out", type=Path, help="Write comparison JSON")
    export = commands.add_parser("export-case", help="Export a manifest and an executable unittest regression")
    export.add_argument("bundle", type=Path)
    export.add_argument("--case", required=True)
    export.add_argument("--out", type=Path, required=True)
    validate = commands.add_parser("validate-pack", help="Validate bounded JSON scenario data without importing contributor code")
    validate.add_argument("path", type=Path)
    mutation = commands.add_parser("mutation-check", help="Execute corrected and declared deliberately faulty controls; save an evidence-backed coverage matrix")
    mutation.add_argument("--pack", default="support-refunds", help="Built-in support-refunds or a validated local manifest directory")
    mutation.add_argument("--case", action="append", dest="cases")
    mutation.add_argument("--seed", action="append", type=int, dest="seeds", help="A supported fixture seed; repeat to execute paired variations")
    mutation.add_argument("--trials", type=int, default=1, help="1–20 fresh trials per scheduled case/control/seed pair")
    mutation.add_argument("--all-pairs", action="store_true", help="Also execute controls not mapped to a case; at most 500 total trials")
    mutation.add_argument("--timeout", type=float, default=5.0)
    mutation.add_argument("--out", type=Path, default=Path("artifacts/coverage"))
    prepare = commands.add_parser("prepare-upload", help="Create a minimized report file locally for review; does not contact a server")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--case", required=True)
    prepare.add_argument("--trial", type=int, default=1, help="One-based occurrence of this case in the recorded bundle")
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--redaction-key-file", type=Path, default=Path(".breakroom/redaction.key"), help="Local private key for stable opaque identifiers; generated if absent, never uploaded")
    upload = commands.add_parser("upload-report", help="Explicitly upload one already prepared and locally reviewed JSON file")
    upload.add_argument("prepared_file", type=Path)
    upload.add_argument("--server", required=True, help="Owner-configured HTTPS origin, without paths or credentials")
    upload.add_argument("--project", required=True)
    upload.add_argument("--token-env", default="BREAKROOM_API_TOKEN", help="Environment variable containing the scoped bearer key; never pass the key as an argument")
    upload.add_argument("--allow-localhost-http", action="store_true", help="Explicitly permit localhost HTTP for local team-service testing only")
    upload.add_argument("--expected-sha256", help="Require this locally reviewed canonical envelope checksum before sending (optional CI review binding)")
    return root


def _doctor() -> int:
    checks = []
    checks.append(("Python 3.11–3.14", (3, 11) <= sys.version_info[:2] < (3, 15), platform.python_version()))
    checks.append(("POSIX process groups", os.name == "posix", platform.system()))
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE health (value INTEGER)")
            connection.execute("INSERT INTO health VALUES (1)")
        checks.append(("SQLite", True, sqlite3.sqlite_version))
    except sqlite3.Error as exc:
        checks.append(("SQLite", False, str(exc)))
    try:
        with tempfile.TemporaryFile(dir=Path.cwd()) as temporary:
            temporary.write(b"breakroom")
        checks.append(("Current directory writable", True, str(Path.cwd())))
    except OSError as exc:
        checks.append(("Current directory writable", False, str(exc)))
    cases = list_cases()
    checks.append(("Built-in Fire Drills", bool(cases), str(len(cases))))
    for name, ok, detail in checks:
        print(f'{"OK" if ok else "ERROR"}  {name}: {detail}')
    print("Core requires no API key, Docker, model service, or cloud account. Customer code is trusted local code; a subprocess is not a sandbox.")
    return 0 if all(ok for _, ok, _ in checks) else 2


def _print_reports(reports: list[dict]) -> None:
    for report in reports:
        print(f'{report["verdict"]:12} {report["case"]["case_id"]} seed={report["seed"]} execution={report["execution"]["status"]}')
    print("Observed trials:", json.dumps(dict(Counter(report["verdict"] for report in reports)), sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor()
        if args.command == "validate-pack":
            cases = load_pack(args.path)
            print(f"Validated {len(cases)} Fire Drills. Scenario manifests are data; no adapter code was imported.")
            return 0 if cases else 2
        if args.command == "prepare-upload":
            from .uploads import load_redaction_key, prepare_upload, save_prepared_upload
            bundle = load_bundle(args.bundle)
            selected = [report for report in bundle["reports"] if report["case"]["case_id"] == args.case]
            if not 1 <= args.trial <= len(selected):
                raise ValueError("Selected case/trial is absent from this recorded bundle")
            prepared = prepare_upload(selected[args.trial - 1], redaction_key=load_redaction_key(args.redaction_key_file))
            destination = save_prepared_upload(prepared, args.out)
            print(f"Prepared locally: {destination.resolve()}")
            from .uploads import upload_digest
            print(f"Canonical upload SHA-256: {upload_digest(prepared)}")
            print("No report was uploaded. Review this minimized JSON; amounts, currencies, relationships, checks and original provenance hashes remain.")
            print("This is customer-generated evidence. Automated minimization is imperfect and original hashes are not independent certification.")
            return 0
        if args.command == "upload-report":
            from .uploads import upload_prepared_file
            result = upload_prepared_file(args.prepared_file, server=args.server, project_id=args.project,
                                          token_env=args.token_env, allow_localhost_http=args.allow_localhost_http,
                                          expected_sha256=args.expected_sha256)
            print(f"{'Previously uploaded' if result['duplicate'] else 'Uploaded'} customer-generated report {result['id']} to project {result['project_id']}.")
            return 0
        if args.command == "mutation-check":
            from .coverage import execute_matrix, write_matrix
            cases = list_cases() if args.pack == "support-refunds" else load_pack(Path(args.pack))
            if args.cases:
                selected = set(args.cases)
                if not selected <= {case["case_id"] for case in cases}:
                    raise ValueError("Selected case is absent from this pack")
                cases = [case for case in cases if case["case_id"] in selected]
            matrix, reports = execute_matrix(cases, seeds=tuple(args.seeds or [0]), trials=args.trials,
                                             all_pairs=args.all_pairs, timeout=args.timeout)
            destination = write_matrix(matrix, reports, args.out)
            summary = matrix["summary"]
            print(f"Detected {summary['detected_negative_trials']}/{summary['expected_negative_trials']} declared negative-control trials; corrected {summary['passing_corrected_trials']}/{summary['expected_corrected_trials']} PASS.")
            print(f"Executed {summary['executed_trial_count']} trials; incomplete negative observations {summary['incomplete_negative_trials']}; missing detections {summary['missed_negative_trials']}.")
            print(f"Mutation review gate exit {summary['gate_exit']}. This is not a passing release gate for the deliberately faulty agents. Evidence: {destination.resolve()}")
            return summary["gate_exit"]
        if args.command == "demo":
            case = load_case("refund-response-lost")
            print("Scripted reference agents. Simulated customers, records, services, money, and failures.")
            faulty = run_case(case, "breakroom.agents:faulty")
            corrected = run_case(case, "breakroom.agents:corrected")
            write_bundle([faulty], args.output / "faulty", [case["case_id"]])
            write_bundle([corrected], args.output / "corrected", [case["case_id"]])
            write_bundle([faulty, corrected], args.output, [case["case_id"]])
            export_case(faulty, args.output / "regression")
            _print_reports([faulty, corrected])
            comparison = compare_reports([faulty], [corrected])
            (args.output / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
            if faulty["verdict"] == "FAIL" and corrected["verdict"] == "PASS" and comparison["compatible"]:
                print(f"Demonstration completed: the faulty agent FAILS and the corrected agent PASSES. This tutorial exit 0 is not a passing faulty-agent release gate. Evidence: {args.output.resolve()}")
                return 0
            print("ERROR: demonstration did not reproduce both expected controls.", file=sys.stderr)
            return 2
        if args.command == "run":
            validate_agent_reference(args.agent)
            if not 1 <= args.trials <= 20:
                raise ValueError("--trials must be between 1 and 20")
            cases = list_cases() if args.pack == "support-refunds" else load_pack(Path(args.pack))
            if args.cases:
                selected = set(args.cases)
                if not selected <= {case["case_id"] for case in cases}:
                    raise ValueError("Selected case is absent from this pack")
                cases = [case for case in cases if case["case_id"] in selected]
            if not cases or len(cases) * args.trials > 500:
                raise ValueError("Run must contain 1–500 required trials")
            print("Running trusted local adapter code. Tool services and business records are simulated.")
            reports = [run_case(case, args.agent, seed=args.seed, timeout=args.timeout,
                                allow_network=args.allow_network, env_allowlist=tuple(args.env))
                       for case in cases for trial in range(args.trials)]
            bundle = write_bundle(reports, args.out, [case["case_id"] for case in cases])
            _print_reports(reports)
            print(f'Release check exit {bundle["gate_exit"]}. Evidence: {args.out.resolve()}')
            return bundle["gate_exit"]
        if args.command == "compare":
            baseline, candidate = load_bundle(args.baseline), load_bundle(args.candidate)
            comparison = compare_reports(baseline["reports"], candidate["reports"])
            for pair in comparison["pairs"]:
                print(f'{pair["case_id"]} seed={pair["seed"]}: {pair["baseline_verdict"]} → {pair["candidate_verdict"]}; ' + ("compatible" if pair["compatible"] else "INCOMPATIBLE " + ", ".join(pair["incompatibilities"])))
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
            required = sorted(set(baseline["required_cases"]) | set(candidate["required_cases"]))
            candidate_gate = gate_exit(candidate["reports"], required)
            return 1 if candidate_gate == 1 else (2 if not comparison["compatible"] else candidate_gate)
        if args.command == "export-case":
            bundle = load_bundle(args.bundle)
            report = next((report for report in bundle["reports"] if report["case"]["case_id"] == args.case), None)
            if report is None:
                raise ValueError("Case was not recorded in this bundle")
            destination = export_case(report, args.out)
            print(f"Runnable regression exported to {destination.resolve()}")
            print(f"python {destination / 'test_regression.py'} --agent examples.agents.corrected:run")
            return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Breakroom configuration/evidence error: {exc}", file=sys.stderr)
        return 2
    return 2
