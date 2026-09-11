"""Real container tests; opt-in requires an already built operator image."""
import os
import threading
import time
import unittest
from pathlib import Path

from breakroom.evaluator import run_in_process
from breakroom.scenarios import load_case, list_cases
from breakroom_api.sandbox.config import SandboxSettings
from breakroom_api.sandbox.runtime import ContainerAgent, verify_runtime
from breakroom_api.sandbox.sources import validate_archive
from test_boundaries import package


@unittest.skipUnless(os.getenv('BREAKROOM_SANDBOX_TEST_DOCKER') == '1', 'Real container runtime not enabled')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = os.getenv('BREAKROOM_SANDBOX_RUNTIME', 'runsc')
        cls.settings = SandboxSettings(runtime=runtime, development_only=runtime=='runc')
        cls.settings.validate('test')
        cls.image = verify_runtime(cls.settings)
        cls.source = Path('packages/breakroom-core/src/breakroom/agents.py').read_text().replace('from .models import', 'from breakroom.models import')

    def run_agent(self, source, *, entrypoint='agent:run', case='refund-response-lost', deadline=15, model=None, cancel=None):
        pkg = validate_archive(package(source, entrypoint=entrypoint))
        with ContainerAgent(pkg, self.settings, image_id=self.image, model=model) as agent:
            return run_in_process(load_case(case), agent, deadline_seconds=deadline, cancel_signal=cancel,
                                  agent_metadata={'name': 'uploaded-agent', 'version': '1', 'code_hash': pkg.sha256, 'model': None})

    def test_actual_effects_faulty_and_corrected(self):
        for name, verdict in [('faulty', 'FAIL'), ('corrected', 'PASS')]:
            result = self.run_agent(self.source, entrypoint='agent:'+name)
            self.assertEqual(result['verdict'], verdict, str(result['execution']))
            self.assertEqual(result['final_state']['refunds'][0]['amount_minor'], 100000)

    def test_all_24_drills(self):
        for case in list_cases():
            with self.subTest(case=case['case_id']):
                result = self.run_agent(self.source, entrypoint='agent:corrected', case=case['case_id'])
                self.assertEqual(result['verdict'], 'PASS', result['execution'])

    def test_concurrent_handlers(self):
        result = self.run_agent(self.source, entrypoint='agent:corrected', case='concurrent-same-request')
        self.assertEqual(result['verdict'], 'PASS', result['execution'])

    def test_model_broker(self):
        calls = []
        source = self.source + '\ndef run(task, tools, context):\n    assert context.model("fixture prompt") == "fixture answer"\n    return corrected(task, tools, context)\n'
        result = self.run_agent(source, model=lambda prompt, **options: calls.append(prompt) or 'fixture answer')
        self.assertEqual(result['verdict'], 'PASS', result['execution'])
        self.assertEqual(calls, ['fixture prompt'])

    def test_no_oracle_keys_network_or_root_write(self):
        source = self.source + '''
def run(task, tools, context):
    import os, socket, importlib.util
    assert os.getuid() == 65532
    assert not any('KEY' in key or 'TOKEN' in key or 'DATABASE' in key for key in os.environ)
    assert importlib.util.find_spec('breakroom.evaluator') is None
    assert not os.path.exists('/var/run/docker.sock')
    try:
        open('/runner/tampered', 'w').write('bad')
        raise AssertionError('writable root')
    except OSError: pass
    sock = socket.socket(); sock.settimeout(.2)
    try:
        sock.connect(('1.1.1.1', 443))
        raise AssertionError('network reachable')
    except OSError: pass
    finally: sock.close()
    return corrected(task, tools, context)
'''
        result = self.run_agent(source)
        self.assertEqual(result['verdict'], 'PASS', result['execution'])

    def test_spin_timeout_and_forged_result_cannot_pass(self):
        start = time.monotonic()
        result = self.run_agent('def run(*args):\n    while True: pass', deadline=1)
        self.assertEqual(result['execution']['status'], 'timed_out')
        self.assertNotEqual(result['verdict'], 'PASS')
        self.assertLess(time.monotonic()-start, 20)
        result = self.run_agent('def run(*args): return {"verdict":"PASS"}')
        self.assertNotEqual(result['verdict'], 'PASS')

    def test_cancel_and_oversized_protocol(self):
        cancelled = threading.Event(); cancelled.set()
        result = self.run_agent('def run(*args):\n    while True: pass', cancel=cancelled)
        self.assertEqual(result['execution']['status'], 'cancelled')
        result = self.run_agent('import sys\ndef run(*args):\n    sys.__stdout__.write("x"*70000+"\\n"); sys.__stdout__.flush()')
        self.assertNotEqual(result['verdict'], 'PASS')
