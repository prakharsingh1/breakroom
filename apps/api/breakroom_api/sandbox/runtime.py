"""Trusted RPC host. Customer code runs only in an operator-owned container.

The simulator and independent evaluator stay here. RPC tools execute in the
original invocation thread so simultaneous handlers preserve simulator state.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import os
import queue
import re
import select
import time
import subprocess
import threading
import uuid

from breakroom.models import AgentResult, ToolError, BudgetExceeded, AgentCancelled

from .sources import bounded_json

TOOLS = {
    "find_customers", "get_customer", "get_order", "list_refunds", "create_refund",
    "get_refund", "get_ticket", "append_note", "update_ticket", "escalate", "receive_events", "ack_event",
}


def command(args, *, timeout=30):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, timeout=timeout, check=True)
        return result.stdout.decode()
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError("Sandbox container operation failed") from exc


def verify_runtime(settings):
    runtimes = json.loads(command(["info", "--format", "{{json .Runtimes}}"], timeout=10))
    if settings.runtime not in runtimes:
        raise RuntimeError("Configured sandbox runtime is not installed")
    image_id = command(["image", "inspect", "--format", "{{.Id}}", settings.image], timeout=10).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise RuntimeError("Sandbox image must resolve to an immutable image ID")
    return image_id


def reap_expired(scope):
    # Recover owned containers after a worker crash. A process supervisor must
    # restart the worker; the customer process cannot change Docker labels.
    listing = command(['ps', '-a', '--filter', 'label=breakroom.sandbox=1', '--filter', 'label=breakroom.scope='+scope,
        '--format', '{{.Names}} {{.Label "breakroom.expires"}}'], timeout=10)
    for line in listing.splitlines():
        parts = line.split()
        if len(parts)==2 and re.fullmatch(r'breakroom-agent-[a-f0-9]{32}',parts[0]) and re.fullmatch(r'[0-9]{10}',parts[1]):
            if int(parts[1]) <= time.time(): command(['rm','-f',parts[0]], timeout=15)


def dispatch(method, args, tools, context, model):
    if not isinstance(args, dict) or len(args) > 8 or any(not isinstance(k, str) for k in args):
        raise ValueError("Invalid tool arguments")
    if method == "model.generate":
        if set(args) != {"prompt"} or not isinstance(args["prompt"], str) or len(args["prompt"].encode()) > 16384:
            raise ValueError("Invalid model prompt")
        context.check_budget()
        if model is None:
            raise RuntimeError("Model access is disabled for this run")
        return model(args["prompt"], timeout_seconds=max(.01, context._wall_deadline-time.monotonic()))
    for key, value in args.items():
        if value is not None and type(value) not in {str, int, float, bool}:
            raise ValueError("Tool arguments must be bounded primitive values")
        if isinstance(value, str) and len(value.encode()) > 4096:
            raise ValueError("Tool argument exceeds its size limit")
        if isinstance(value, (int, float)) and abs(value) > 10**12:
            raise ValueError("Tool number exceeds its supported range")
        if key in {"amount_minor", "expected_version", "limit"} and value is not None and type(value) is not int:
            raise ValueError("Tool integer argument required")
        if key not in {"amount_minor", "expected_version", "limit", "seconds"} and value is not None and not isinstance(value, str):
            raise ValueError("Tool text argument required")
    context.check_budget()
    if method in TOOLS:
        if method == "receive_events" and not 1 <= args.get("limit", 8) <= 64:
            raise ValueError("Event batch exceeds its supported range")
        return getattr(tools, method)(**args)
    if method in {"context.now", "context.sleep", "context.check_budget"}:
        return getattr(context, method.split(".")[1])(**args)
    if method == "context.cancelled" and not args:
        return context.cancelled
    raise ValueError("Tool is not approved")


class ContainerAgent:
    def __init__(self, package, settings, *, image_id=None, model=None, scope="fixture"):
        self.scope = scope
        self.package, self.settings, self.model = package, settings, model
        self.image_id = image_id or verify_runtime(settings)
        self.capabilities = frozenset(package.manifest["capabilities"])
        self.name = "breakroom-agent-" + uuid.uuid4().hex
        self.lock = threading.Lock()
        self.inboxes = {}
        self.failed = threading.Event()
        self.process = None
        self.error = "Sandbox agent disconnected or exceeded protocol limits"

    def __enter__(self):
        try:
            command(["create", "--name", self.name, "--label", "breakroom.sandbox=1",
                "--label", "breakroom.scope=" + self.scope, "--label", "breakroom.expires=" + str(int(time.time())+120),
                "--runtime", self.settings.runtime, "--network", "none", "--read-only", "--user", "65532:65532",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "512m", "--memory-swap", "512m",
                "--cpus", "1", "--pids-limit", "64", "--ulimit", "nofile=128:128", "--ulimit", "core=0:0",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m", "-i", "--log-driver", "none", self.image_id])
            self.process = subprocess.Popen(["docker", "start", "-ai", self.name], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            self.reader = threading.Thread(target=self._read, daemon=True, name="sandbox-rpc")
            self.stderr = threading.Thread(target=self._drain, daemon=True, name="sandbox-stderr")
            self.reader.start()
            self.stderr.start()
            self._send({"kind": "source", "files": {name: base64.b64encode(content).decode() for name, content in self.package.files.items()}}, limit=6*1024*1024)
            return self
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for _ in range(2048):
                raw = self.process.stdout.readline(65538)
                if not raw or len(raw) > 65537:
                    break
                value = bounded_json(raw)
                if not isinstance(value, dict) or value.get("kind") not in {"call", "result", "error"}:
                    break
                invocation = value.get("invocation")
                if not isinstance(invocation, str):
                    break
                with self.lock:
                    inbox = self.inboxes.get(invocation)
                if inbox is None:
                    break
                inbox.put_nowait(value)
        except (ValueError, OSError, queue.Full):
            pass
        finally:
            self.failed.set()

    def _drain(self):
        try:
            remaining = 65536
            while remaining > 0:
                raw = self.process.stderr.read(min(4096, remaining))
                if not raw:
                    return
                remaining -= len(raw)
            self.failed.set()
        except OSError:
            self.failed.set()

    def _send(self, value, *, limit=65537):
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(raw) > limit:
            raise RuntimeError("Sandbox tool response exceeds protocol bounds")
        # Host messages are bounded and the guest continuously drains stdin.
        # A stuck/non-reading guest cannot block the host: use an OS nonblocking pipe.
        with self.lock:
            fd = self.process.stdin.fileno()
            os.set_blocking(fd, False)
            try:
                offset, deadline = 0, time.monotonic() + 5
                while offset < len(raw):
                    if self.failed.is_set() or time.monotonic() >= deadline:
                        raise RuntimeError("Sandbox agent stopped reading tool responses")
                    if select.select([], [fd], [], .05)[1]:
                        try:
                            offset += os.write(fd, raw[offset:offset+4096])
                        except BlockingIOError:
                            continue
            except (OSError, BrokenPipeError) as exc:
                raise RuntimeError(self.error) from exc

    def __call__(self, task, tools, context):
        invocation = uuid.uuid4().hex
        inbox = queue.Queue(maxsize=16)
        with self.lock:
            self.inboxes[invocation] = inbox
        try:
            self._send({"kind": "invoke", "invocation": invocation, "task": dataclasses.asdict(task),
                "context": {"logical_operation_id": context.logical_operation_id, "deadline": context.deadline}})
            calls = 0
            while True:
                context.check_budget()
                if self.failed.is_set():
                    raise RuntimeError(self.error)
                try:
                    value = inbox.get(timeout=0.05)
                except queue.Empty:
                    continue
                if value["kind"] == "result":
                    candidate = value.get("value")
                    if candidate is None:
                        return None
                    if not isinstance(candidate, dict) or set(candidate) != {"claims", "evidence_refs", "escalated", "customer_text"}:
                        raise RuntimeError("Sandbox agent returned an invalid result")
                    return AgentResult(**candidate)
                if value["kind"] == "error":
                    raise RuntimeError("Sandbox adapter raised an exception")
                calls += 1
                if calls > 256 or not isinstance(value.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["id"]):
                    raise RuntimeError("Sandbox tool-call limit exceeded or invalid request ID")
                reply = {"kind": "reply", "id": value["id"]}
                try:
                    reply["value"] = dispatch(value.get("method"), value.get("args"), tools, context, self.model)
                except ToolError as exc:
                    reply["error"] = {"type": "ToolError", "code": exc.code, "message": str(exc), "retry_after": exc.retry_after}
                except (BudgetExceeded, AgentCancelled):
                    raise
                except Exception:
                    reply["error"] = {"type": "Error", "message": "Tool or model request rejected or unavailable"}
                self._send(reply)
        finally:
            with self.lock:
                self.inboxes.pop(invocation, None)

    def close(self):
        self.failed.set()
        try:
            command(["rm", "-f", self.name], timeout=15)
        finally:
            if self.process:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
                self.reader.join(timeout=2)
                self.stderr.join(timeout=2)
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    stream.close()

    def __exit__(self, *_):
        self.close()
