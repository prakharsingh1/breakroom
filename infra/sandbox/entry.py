"""Untrusted-side adapter bridge. This process has no oracle, database or keys."""
import base64
import os
from pathlib import Path
import dataclasses
import importlib
import json
import queue
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from breakroom.models import AgentResult, TaskEnvelope, SupportTools, ToolError, BudgetExceeded, AgentCancelled

wire = sys.stdout
sys.stdout = sys.stderr
lock = threading.Lock()
pending = {}
local = threading.local()


def send(value):
    raw = json.dumps(value, allow_nan=False, separators=(",", ":"))
    if len(raw.encode()) > 65536:
        raise ValueError("Adapter message too large")
    with lock:
        wire.write(raw + "\n")
        wire.flush()


def call(method, **args):
    ident = uuid.uuid4().hex
    inbox = queue.Queue(maxsize=1)
    with lock:
        pending[ident] = inbox
    try:
        send({"kind": "call", "invocation": local.invocation, "id": ident, "method": method, "args": args})
        result = inbox.get(timeout=125)
        if "error" in result:
            error = result["error"]
            if error["type"] == "ToolError":
                raise ToolError(error["code"], error["message"], retry_after=error.get("retry_after", 0))
            if error["type"] == "BudgetExceeded":
                raise BudgetExceeded(error["message"])
            if error["type"] == "AgentCancelled":
                raise AgentCancelled(error["message"])
            raise RuntimeError(error["message"])
        return result["value"]
    finally:
        with lock:
            pending.pop(ident, None)


class Context:
    def __init__(self, value):
        self.logical_operation_id = value["logical_operation_id"]
        self.deadline = value["deadline"]
    def now(self): return call("context.now")
    def sleep(self, seconds): return call("context.sleep", seconds=seconds)
    def check_budget(self): return call("context.check_budget")
    @property
    def cancelled(self): return call("context.cancelled")
    def model(self, prompt): return call("model.generate", prompt=prompt)


bootstrap = json.loads(sys.stdin.buffer.readline(6*1024*1024+1))
os.mkdir('/tmp/agent')
for name, encoded in bootstrap['files'].items():
    target = Path('/tmp/agent') / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(encoded))
    target.chmod(0o444)
os.chdir('/tmp/agent')
os.environ.clear()
os.environ['PATH'] = '/usr/local/bin:/usr/bin:/bin'
with open("/tmp/agent/breakroom-agent.json") as source:
    manifest = json.load(source)
sys.path.append("/tmp/agent")
module, function = manifest["entrypoint"].split(":")
agent = getattr(importlib.import_module(module), function)


def invoke(value):
    local.invocation = value["invocation"]
    try:
        result = agent(TaskEnvelope(**value["task"]), SupportTools(call), Context(value["context"]))
        send({"kind": "result", "invocation": local.invocation,
              "value": dataclasses.asdict(result) if isinstance(result, AgentResult) else None})
    except BaseException:
        # Exception strings may contain provider output/source. Do not leak them.
        send({"kind": "error", "invocation": local.invocation})


with ThreadPoolExecutor(max_workers=4) as executor:
    while True:
        raw = sys.stdin.buffer.readline(65538)
        if not raw:
            break
        if len(raw) > 65537:
            raise ValueError("Oversized message")
        value = json.loads(raw)
        if value["kind"] == "invoke":
            executor.submit(invoke, value)
        elif value["kind"] == "reply":
            with lock:
                inbox = pending.get(value["id"])
            if inbox:
                inbox.put_nowait(value)
