"""Local trusted adapters used only by runner contract tests."""
import os
from pathlib import Path
import subprocess
import sys
import time


def hung(task, tools, context):
    while True:
        time.sleep(0.1)


def spawn_child(task, tools, context):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(task.text).write_text(str(process.pid))
    while True:
        time.sleep(0.1)


def no_secrets(task, tools, context):
    from breakroom.agents import corrected
    for key in ("STRIPE_SECRET_KEY", "OPENAI_API_KEY", "ZENDESK_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        if key in os.environ:
            raise RuntimeError("Service secret inherited")
    return corrected(task, tools, context)


def flood(task, tools, context):
    while True:
        print("x" * 8192, flush=True)


def crash(task, tools, context):
    os._exit(17)


def duplicate_then_hang(task, tools, context):
    for suffix in ("first", "second", "third"):
        try:
            tools.create_refund(task.order_id, task.amount_minor, task.currency,
                                context.logical_operation_id + suffix, task.request_id, task.customer_id)
        except Exception:
            pass
    while True:
        time.sleep(0.1)
