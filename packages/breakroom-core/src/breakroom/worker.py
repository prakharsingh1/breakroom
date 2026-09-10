"""Internal worker protocol for trusted local code. Never expose this as an API."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys

from .runner import validate_agent_reference
from .reports import read_json


def main() -> int:
    from .evaluator import run_in_process, evaluate_recovered
    from .scenarios import validate_case

    if len(sys.argv) != 2:
        return 2
    request = read_json(Path(sys.argv[1]))
    case = request["case"]
    validate_case(case)
    module_name, function_name = validate_agent_reference(request["agent"]).split(":")
    metadata = request["agent_metadata"]
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
        if not callable(function):
            raise ValueError("Agent reference does not resolve to a callable")
        metadata["adapter_version"] = getattr(module, "__version__", None)
        source = inspect.getsourcefile(function)
        if source and Path(source).is_file():
            metadata["code_hash"] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        report = run_in_process(case, function, request["seed"], request["db_path"],
                                deadline_seconds=request["timeout"], agent_metadata=metadata)
    except Exception as exc:
        report = evaluate_recovered(case, request["db_path"], request["seed"], execution="errored",
                                    error=f"Adapter/worker error: {type(exc).__name__}", agent_metadata=metadata)
    destination = Path(request["result_path"])
    temporary = destination.with_suffix(".pending")
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 8 * 1024 * 1024:
        return 2
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
