"""Bounded data reports, escaped renderers, compatible comparisons, and exports."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "1.0"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_JSON_NODES = 250_000
# Bundles have four times the byte allowance of an individual report. Keep a
# separate finite aggregate node budget so a full 500-trial run can be read.
MAX_BUNDLE_NODES = 1_000_000
VERDICTS = {"PASS", "FAIL", "INCONCLUSIVE", "UNSUPPORTED"}
REPORT_REQUIRED = {"schema_version", "producer", "engine_version", "oracle_version", "run_id", "case", "agent", "seed", "execution", "initial_state", "events", "final_state", "checks", "verdict", "limitations", "metrics"}
REPORT_OPTIONAL = {"environment", "created_at", "fault_coverage", "agent_result", "result", "trial_count", "config_hash", "simulator_version"}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def read_json(path: str | Path, *, max_bytes: int = MAX_JSON_BYTES,
              max_nodes: int = MAX_JSON_NODES):
    if type(max_nodes) is not int or not 1 <= max_nodes <= MAX_BUNDLE_NODES:
        raise ValueError("JSON node budget must be positive and at most the bundle limit")
    with Path(path).open("rb") as source:
        content = source.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError(f"JSON exceeds {max_bytes} bytes")
    try:
        value = json.loads(content, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid UTF-8 JSON data") from exc
    _bounded(value, budget=[max_nodes])
    return value


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [MAX_JSON_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > 24:
        raise ValueError("JSON structure exceeds node/depth limit")
    if isinstance(value, dict):
        if len(value) > 1000:
            raise ValueError("Too many object fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise ValueError("Invalid JSON object key")
            if key.endswith("amount_minor") and item is not None and type(item) is not int:
                raise ValueError("Money must use integer minor units")
            _bounded(item, depth + 1, budget)
    elif isinstance(value, list):
        if len(value) > 20000:
            raise ValueError("Too many array values")
        for item in value:
            _bounded(item, depth + 1, budget)
    elif isinstance(value, str):
        if len(value) > 65536:
            raise ValueError("String exceeds 65536 characters")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("Invalid Unicode surrogate in report")
    elif value is None or type(value) in (int, bool):
        pass
    elif isinstance(value, float) and math.isfinite(value):
        pass
    else:
        raise ValueError("Report contains a non-JSON or non-finite value")


def validate_report(report: dict) -> dict:
    _bounded(report)
    if not isinstance(report, dict) or not REPORT_REQUIRED <= report.keys():
        raise ValueError("Report is missing required schema fields")
    unknown = report.keys() - REPORT_REQUIRED - REPORT_OPTIONAL
    if unknown:
        raise ValueError(f"Unknown report fields: {sorted(unknown)}")
    if report["schema_version"] != SCHEMA_VERSION or report["producer"] != "breakroom":
        raise ValueError("Unsupported report schema or producer")
    for field in ("engine_version", "oracle_version", "run_id"):
        if not isinstance(report[field], str) or not 1 <= len(report[field]) <= 256:
            raise ValueError(f"Invalid report {field}")
    for field in ("engine_version", "oracle_version"):
        if not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}(?:\.[0-9]{1,4})?(?:[-+][A-Za-z0-9.-]{1,40})?", report[field]):
            raise ValueError(f"Invalid {field} contract version")
    if type(report["seed"]) is not int or not 0 <= report["seed"] <= 2**31 - 1:
        raise ValueError("Invalid report seed")
    if not isinstance(report["case"], dict):
        raise ValueError("Invalid report case")
    for field in ("case_id", "case_version", "pack_version", "manifest_hash", "fixture_hash"):
        if not isinstance(report["case"].get(field), str) or not report["case"][field]:
            raise ValueError(f"Invalid case {field}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", report["case"]["case_id"]):
        raise ValueError("Invalid case ID")
    if "manifest" not in report["case"] or not isinstance(report["case"]["manifest"], dict):
        raise ValueError("Report must contain its scenario manifest")
    from .scenarios import content_hash, validate_case
    validate_case(report["case"]["manifest"])
    manifest = report["case"]["manifest"]
    for field in ("case_version", "pack_version"):
        if report["case"][field] != manifest[field]:
            raise ValueError("Manifest version disagrees with report")
    if report["case"]["manifest_hash"] != content_hash(manifest) or report["case"]["fixture_hash"] != content_hash(manifest["initial_state"]):
        raise ValueError("Manifest or fixture checksum does not match report")
    if report["initial_state"] != manifest["initial_state"]:
        raise ValueError("Initial state disagrees with frozen fixture")
    provenance_fields = {"source_manifest", "source_manifest_hash", "generator_version"}
    if provenance_fields & report["case"].keys():
        if not provenance_fields <= report["case"].keys():
            raise ValueError("Variation provenance must include source manifest, hash and generator version")
        source = validate_case(report["case"]["source_manifest"])
        if content_hash(source) != report["case"]["source_manifest_hash"]:
            raise ValueError("Source manifest checksum does not match")
        from .variations import GENERATOR_VERSION, materialize_case
        if report["case"]["generator_version"] != GENERATOR_VERSION:
            raise ValueError("Unsupported fixture generator version")
        if materialize_case(source, report["seed"]) != manifest:
            raise ValueError("Materialized manifest disagrees with its recorded source and seed")
    if report["case"]["manifest"].get("case_id") != report["case"]["case_id"]:
        raise ValueError("Case manifest ID disagrees with report")
    if not isinstance(report["agent"], dict) or not isinstance(report["metrics"], dict):
        raise ValueError("Invalid agent or metrics metadata")
    if not isinstance(report["execution"], dict) or report["execution"].get("status") not in {"completed", "errored", "timed_out", "cancelled"}:
        raise ValueError("Invalid execution status")
    for field in ("initial_state", "final_state"):
        if not isinstance(report[field], dict):
            raise ValueError(f"Invalid {field}")
    if not isinstance(report["events"], list) or any(not isinstance(event, dict) for event in report["events"]):
        raise ValueError("Invalid event sequence")
    if not isinstance(report["limitations"], list) or any(not isinstance(item, str) for item in report["limitations"]):
        raise ValueError("Invalid limitations")
    checks = report["checks"]
    if not isinstance(checks, list) or len(checks) > 200:
        raise ValueError("Invalid checks")
    for check in checks:
        if not isinstance(check, dict) or not {"id", "category", "status", "message", "evidence_refs"} <= check.keys():
            raise ValueError("Check is missing required fields")
        if check["status"] not in {"pass", "fail", "unknown", "not_applicable"}:
            raise ValueError("Invalid check status")
        if any(not isinstance(check[key], str) for key in ("id", "category", "message")):
            raise ValueError("Invalid check text")
        if not isinstance(check["evidence_refs"], list) or any(not isinstance(ref, str) for ref in check["evidence_refs"]):
            raise ValueError("Invalid check evidence references")
    if len({check["id"] for check in checks}) != len(checks):
        raise ValueError("Duplicate independent check ID")
    expected_checks = set(manifest["assertions"]) | {"fault_coverage", "adapter_capabilities", "execution_completed", "logical_deadline"}
    if "exact_amount" in expected_checks:
        expected_checks.add("amount_authorization")
    if "ticket_consistent" in expected_checks:
        expected_checks.add("ticket_target")
    if not expected_checks <= {check["id"] for check in checks}:
        raise ValueError("Report omits required independent checks")
    if report["verdict"] not in VERDICTS:
        raise ValueError("Invalid verdict")
    if report["verdict"] == "PASS" and (not checks or report["execution"]["status"] != "completed" or any(check["status"] in {"fail", "unknown"} for check in checks) or not any(check["status"] == "pass" for check in checks)):
        raise ValueError("PASS contradicts incomplete execution or checks")
    return report


def gate_exit(reports: list[dict], required_cases: list[str] | None = None) -> int:
    """Known failure (1) precedes incomplete/invalid evidence (2); all pass is 0."""
    if any(report.get("verdict") == "FAIL" for report in reports):
        return 1
    if not reports or any(report.get("verdict") != "PASS" for report in reports):
        return 2
    present = {report["case"]["case_id"] for report in reports}
    if required_cases is not None and (not required_cases or not set(required_cases) <= present):
        return 2
    return 0


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def render_html(reports: list[dict]) -> str:
    sections = []
    for report in reports:
        validate_report(report)
        title = report["case"]["manifest"].get("name", report["case"]["case_id"])
        checks = "".join(f'<tr><td>{escape(check["id"])}</td><td>{escape(check["status"])}</td><td>{escape(check["message"])}</td></tr>' for check in report["checks"])
        sections.append(f'<section><h2>Fire Drill — {escape(title)}</h2><p><strong>{escape(report["verdict"])}</strong> · execution {escape(report["execution"]["status"])}</p><table><caption>Independent outcome checks</caption><thead><tr><th>Check</th><th>Status</th><th>Evidence</th></tr></thead><tbody>{checks}</tbody></table><details><summary>Full recorded evidence</summary><pre>{escape(_json(report))}</pre></details></section>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;"><title>Breakroom — Evidence report</title><style>body{font:16px system-ui,sans-serif;background:#f7f7f2;color:#17201f;max-width:1000px;margin:40px auto;padding:0 20px}section{border-top:1px solid #ccd0ca;padding:24px 0}table{border-collapse:collapse;width:100%;text-align:left}th,td{padding:12px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101a1c;color:#eee;padding:20px}summary{cursor:pointer;margin-top:20px}:focus-visible{outline:3px solid #5b50e5}</style><main><h1>breakroom · Evidence</h1><p>Recorded test executions. Simulated customers, services, money, and faults. Reference agents are scripted; this is not a commercial-model benchmark.</p>' + "".join(sections) + '</main></html>\n'


def _xml_text(value: str) -> str:
    return "".join(character if character in "\t\n\r" or 0x20 <= ord(character) <= 0xD7FF or 0xE000 <= ord(character) <= 0xFFFD or 0x10000 <= ord(character) <= 0x10FFFF else "\ufffd" for character in value)


def render_junit(reports: list[dict]) -> str:
    suite = ET.Element("testsuite", name="Breakroom Fire Drills", tests=str(len(reports)),
                       failures=str(sum(r["verdict"] == "FAIL" for r in reports)),
                       errors=str(sum(r["verdict"] in {"INCONCLUSIVE", "UNSUPPORTED"} for r in reports)))
    for report in reports:
        validate_report(report)
        item = ET.SubElement(suite, "testcase", classname="breakroom", name=f'{report["case"]["case_id"]}[seed={report["seed"]}]')
        if report["verdict"] != "PASS":
            kind = "failure" if report["verdict"] == "FAIL" else "error"
            child = ET.SubElement(item, kind, type=report["verdict"], message=report["verdict"])
            child.text = "\n".join(f'{c["id"]}: {c["status"]}: {c["message"]}' for c in report["checks"] if c["status"] != "pass")
        ET.SubElement(item, "system-out").text = _json({"run_id": report["run_id"], "execution": report["execution"], "verdict": report["verdict"]})
    for element in suite.iter():
        if element.text:
            element.text = _xml_text(element.text)
        element.attrib.update({key: _xml_text(value) for key, value in element.attrib.items()})
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(suite, encoding="unicode") + "\n"


def write_bundle(reports: list[dict], out: str | Path, required_cases: list[str] | None = None) -> dict:
    if len(reports) > 500:
        raise ValueError("Maximum 500 trials per bundle")
    for report in reports:
        validate_report(report)
    required = required_cases if required_cases is not None else sorted({r["case"]["case_id"] for r in reports})
    bundle = {"schema_version": SCHEMA_VERSION, "producer": "breakroom", "kind": "run_bundle", "created_at": datetime.now(timezone.utc).isoformat(), "required_cases": required, "trial_count": len(reports), "gate_exit": gate_exit(reports, required), "reports": reports}
    # Never write a bundle which the bounded reader cannot subsequently load.
    _bounded(bundle, budget=[MAX_BUNDLE_NODES])
    encoded = _json(bundle)
    if len(encoded.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise ValueError("Run bundle exceeds 32 MiB")
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(encoded, encoding="utf-8")
    (destination / "report.html").write_text(render_html(reports), encoding="utf-8")
    (destination / "junit.xml").write_text(render_junit(reports), encoding="utf-8")
    return bundle


def load_bundle(path: str | Path) -> dict:
    source = Path(path)
    if source.is_dir():
        source = source / "report.json"
    bundle = read_json(source, max_bytes=MAX_BUNDLE_BYTES, max_nodes=MAX_BUNDLE_NODES)
    fields = {"schema_version", "producer", "kind", "created_at", "required_cases", "trial_count", "gate_exit", "reports"}
    if not isinstance(bundle, dict) or set(bundle) != fields or bundle["schema_version"] != SCHEMA_VERSION or bundle["producer"] != "breakroom" or bundle["kind"] != "run_bundle":
        raise ValueError("Invalid run bundle schema")
    if not isinstance(bundle["reports"], list) or len(bundle["reports"]) > 500:
        raise ValueError("Invalid bundle trials")
    if not isinstance(bundle["required_cases"], list) or any(not isinstance(c, str) for c in bundle["required_cases"]) or len(bundle["required_cases"]) > 200:
        raise ValueError("Invalid required case coverage")
    for report in bundle["reports"]:
        validate_report(report)
    if type(bundle["trial_count"]) is not int or bundle["trial_count"] != len(bundle["reports"]) or bundle["gate_exit"] != gate_exit(bundle["reports"], bundle["required_cases"]):
        raise ValueError("Bundle summary contradicts recorded trials")
    return bundle


def compare_reports(baseline: list[dict], candidate: list[dict]) -> dict:
    for report in baseline + candidate:
        validate_report(report)
    def keyed(reports):
        index = {}
        occurrences = Counter()
        for report in reports:
            pair = (report["case"]["case_id"], report["seed"])
            occurrences[pair] += 1
            key = (*pair, occurrences[pair])
            index[key] = report
        return index
    left, right = keyed(baseline), keyed(candidate)
    pairs = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        differences = []
        if a is None or b is None:
            differences.append("missing_paired_trial")
        else:
            for field in ("schema_version", "engine_version", "oracle_version"):
                if a[field] != b[field]:
                    differences.append(field)
            for field in ("case_version", "pack_version", "manifest_hash", "fixture_hash"):
                if a["case"][field] != b["case"][field]:
                    differences.append(field)
        pair = {"case_id": key[0], "seed": key[1], "trial_index": key[2], "compatible": not differences, "incompatibilities": differences,
                "baseline_verdict": a["verdict"] if a else None, "candidate_verdict": b["verdict"] if b else None,
                "changed_checks": [], "changed_effects": None}
        if not differences:
            a_checks = {c["id"]: c["status"] for c in a["checks"]}
            b_checks = {c["id"]: c["status"] for c in b["checks"]}
            pair["changed_checks"] = [{"id": check_id, "baseline": a_checks.get(check_id), "candidate": b_checks.get(check_id)} for check_id in sorted(a_checks.keys() | b_checks.keys()) if a_checks.get(check_id) != b_checks.get(check_id)]
            pair["changed_effects"] = {"baseline": a["final_state"], "candidate": b["final_state"]} if a["final_state"] != b["final_state"] else {}
        pairs.append(pair)
    return {"schema_version": SCHEMA_VERSION, "producer": "breakroom", "compatible": bool(pairs) and all(p["compatible"] for p in pairs), "pairs": pairs,
            "baseline_counts": dict(Counter(r["verdict"] for r in baseline)), "candidate_counts": dict(Counter(r["verdict"] for r in candidate)),
            "baseline_trial_count": len(baseline), "candidate_trial_count": len(candidate),
            "limitations": ["Paired observed trials; no statistical significance or general model determinism is claimed."]}


REGRESSION_TEST = '''"""Breakroom exported regression. Local adapter imports execute trusted code."""
import argparse
import json
from pathlib import Path
import unittest

from breakroom.runner import run_case
from breakroom.scenarios import load_case
from breakroom.models import ENGINE_VERSION

EXPECTED_ENGINE_VERSION = __ENGINE_LITERAL__
EXPECTED_ORACLE_VERSION = __ORACLE_LITERAL__
EXPECTED_FIXTURE_HASH = __FIXTURE_LITERAL__
AGENT = "breakroom.agents:corrected"
MANIFEST = Path(__file__).with_name("case.json")
SEED = 0

class ExportedRegression(unittest.TestCase):
    def test_authorized_business_outcome(self):
        self.assertEqual(ENGINE_VERSION, EXPECTED_ENGINE_VERSION, "Install the exported engine version")
        report = run_case(load_case(MANIFEST), AGENT, seed=SEED)
        self.assertEqual(report["oracle_version"], EXPECTED_ORACLE_VERSION, "Install the exported oracle version")
        self.assertEqual(report["case"]["fixture_hash"], EXPECTED_FIXTURE_HASH, "Exported fixture/seed did not reproduce")
        self.assertEqual(report["verdict"], "PASS", json.dumps(report["checks"], indent=2))
        self.assertTrue(report["checks"], "No checks executed")
        self.assertFalse(any(check["status"] in {"fail", "unknown"} for check in report["checks"]))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Execute this exported Fire Drill against trusted local agent code")
    parser.add_argument("--agent", default=AGENT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--seed", type=int, default=SEED)
    args, remaining = parser.parse_known_args()
    AGENT, MANIFEST, SEED = args.agent, args.manifest, args.seed
    unittest.main(argv=[__file__] + remaining)
'''


def export_case(report: dict, out: str | Path) -> Path:
    validate_report(report)
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "case.json").write_text(_json(deepcopy(report["case"].get("source_manifest", report["case"]["manifest"]))), encoding="utf-8")
    (destination / "test_regression.py").write_text(REGRESSION_TEST.replace("SEED = 0\n", f'SEED = {report["seed"]}\n').replace("__ENGINE_LITERAL__", json.dumps(report["engine_version"])).replace("__ORACLE_LITERAL__", json.dumps(report["oracle_version"])).replace("__FIXTURE_LITERAL__", json.dumps(report["case"]["fixture_hash"])), encoding="utf-8")
    (destination / "requirements.txt").write_text("# No third-party runtime dependencies. Requires Python 3.11–3.14 and the local Breakroom core.\n# From the Breakroom checkout: python -m pip install -e ./packages/breakroom-core\n# Do not install an unrelated public package named breakroom.\n", encoding="utf-8")
    (destination / "README.md").write_text(f'''# Breakroom runnable regression

This export freezes `{report["case"]["case_id"]}` version `{report["case"]["case_version"]}`, simulator `{report["engine_version"]}`, oracle `{report["oracle_version"]}`, seed `{report["seed"]}`. It contains bounded JSON data and a unittest that runs a **new** isolated synthetic trial. Recorded evidence is not replayed as a new execution.

From the Breakroom checkout, install the local source (Python 3.11–3.14, macOS/Linux):

```sh
python -m pip install -e ./packages/breakroom-core
python /path/to/export/test_regression.py --agent examples.agents.corrected:run
python /path/to/export/test_regression.py --agent examples.agents.faulty:run
```

Replace `/path/to/export` with this directory. The corrected command should pass this completion regression; a faulty adapter fails when this drill detects its bug. Supply your trusted local adapter with `--agent your_package.adapter:run` and optionally another validated JSON manifest with `--manifest /path/to/case.json`. This is a real state assertion, not an assertion about an agent label.

The script imports the installed Breakroom core; it does not embed the engine. Keep the matching source version with the export. A local subprocess is not a security sandbox. The injected tools use simulated services; customer code may independently access the host or network. Unknown evidence does not pass. A passing drill is limited to its modeled conditions.
''', encoding="utf-8")
    return destination
