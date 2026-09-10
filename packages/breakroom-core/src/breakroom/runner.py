"""Bounded execution of trusted local adapters. A subprocess is not a sandbox."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

AGENT_PATTERN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*\Z", re.ASCII)
MAX_OUTPUT_BYTES = 65536


def validate_agent_reference(reference: str) -> str:
    if not isinstance(reference, str) or len(reference) > 512 or not AGENT_PATTERN.fullmatch(reference):
        raise ValueError("Agent must be a trusted local module:function reference, e.g. examples.agents.corrected:run")
    return reference


def _terminate_group(process: subprocess.Popen) -> None:
    """Reap the worker and stop descendants even if their parent exited normally."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass
    # Descendants may ignore SIGTERM even if the main worker exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1)


def run_case(case: dict, agent: str, *, seed: int = 0, timeout: float = 5.0,
             cancel_signal: Any = None, allow_network: bool = False,
             env_allowlist: tuple[str, ...] = (), working_dir: str | Path | None = None) -> dict:
    """Execute one fresh SQLite trial, evaluate effects, and discard worker storage.

    The local agent import is executable trusted code. Network is not blocked by
    this subprocess; allow_network explicitly opts into passing provider settings.
    Use the documented no-network container for OS-enforced network isolation.
    """
    from .scenarios import validate_case
    from .evaluator import evaluate_recovered
    from .reports import read_json, validate_report

    validate_case(case)
    validate_agent_reference(agent)
    if os.name != "posix":
        raise ValueError("The bounded runner currently supports POSIX (macOS/Linux); use the Linux container on Windows")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0.05 <= timeout <= 120:
        raise ValueError("timeout must be between 0.05 and 120 seconds")
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer between 0 and 2147483647")
    if env_allowlist and not allow_network:
        raise ValueError("Passing provider environment variables requires --allow-network")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) or key in {"PYTHONPATH", "PYTHONHOME", "PATH", "HOME", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"} for key in env_allowlist):
        raise ValueError("Invalid provider environment variable allowlist")
    cwd = Path(working_dir or Path.cwd()).resolve()
    if not cwd.is_dir():
        raise ValueError("Agent working directory must exist")
    started = time.monotonic()
    metadata = {"reference": agent, "adapter_version": None, "code_hash": None, "network_opt_in": allow_network}
    with tempfile.TemporaryDirectory(prefix="breakroom-trial-") as directory:
        trial_dir = Path(directory)
        db_path, result_path = trial_dir / "state.sqlite", trial_dir / "report.json"
        request = {"case": case, "agent": agent, "seed": seed, "timeout": timeout,
                   "db_path": str(db_path), "result_path": str(result_path), "agent_metadata": metadata}
        request_path = trial_dir / "request.json"
        request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
        core_source = Path(__file__).resolve().parent.parent
        environment = {"PATH": os.defpath, "HOME": directory, "TMPDIR": directory,
                       "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": str(seed),
                       "PYTHONPATH": os.pathsep.join((str(core_source), str(cwd))),
                       "BREAKROOM_NETWORK_OPT_IN": "1" if allow_network else "0"}
        for key in env_allowlist:
            if key in os.environ:
                environment[key] = os.environ[key]
        if cancel_signal is not None and cancel_signal.is_set():
            return evaluate_recovered(case, str(db_path), seed, execution="cancelled",
                                      error="Cancelled before worker start", agent_metadata=metadata)
        process = subprocess.Popen([sys.executable, "-m", "breakroom.worker", str(request_path)],
                                   cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        output = bytearray()
        status = None
        error = None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if cancel_signal is not None and cancel_signal.is_set():
                    status, error = "cancelled", "Cancelled by caller"
                    break
                if time.monotonic() - started > timeout:
                    status, error = "timed_out", "Agent exceeded independent wall-clock deadline"
                    break
                for key, _ in selector.select(timeout=0.02):
                    chunk = os.read(key.fd, 8192)
                    output.extend(chunk[:max(0, MAX_OUTPUT_BYTES + 1 - len(output))])
                    if not chunk:
                        selector.unregister(key.fileobj)
                if len(output) > MAX_OUTPUT_BYTES:
                    status, error = "errored", "Worker output exceeded 65536 bytes"
                    break
                if process.poll() is not None:
                    break
        except KeyboardInterrupt:
            status, error = "cancelled", "Cancelled by keyboard interrupt"
        finally:
            selector.close()
            _terminate_group(process)
            process.stdout.close()
        if status is None and process.returncode == 0 and result_path.exists():
            try:
                report = validate_report(read_json(result_path))
                report["execution"]["wall_duration_ms"] = round((time.monotonic() - started) * 1000)
                return report
            except (ValueError, OSError) as exc:
                status, error = "errored", f"Worker returned an invalid report: {exc}"
        if status is None:
            status, error = "errored", f"Worker exited without a valid report (exit {process.returncode})"
        report = evaluate_recovered(case, str(db_path), seed, execution=status, error=error, agent_metadata=metadata)
        report["execution"]["wall_duration_ms"] = round((time.monotonic() - started) * 1000)
        # stdout is intentionally not promoted into reports: customer code can print secrets.
        return validate_report(report)
