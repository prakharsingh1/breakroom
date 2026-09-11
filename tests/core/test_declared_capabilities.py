"""Schema-1 adapters must honor declared capability coverage too."""
import unittest
from breakroom.agents import corrected
from breakroom.evaluator import run_in_process
from breakroom.models import SupportTools
from breakroom.scenarios import load_case

class DeclaredCapabilities(unittest.TestCase):
    def test_missing_capabilities_do_not_execute_or_pass(self):
        calls=[]
        def adapter(*args):
            calls.append(True)
            return corrected(*args)
        adapter.capabilities=frozenset()
        for case in ['normal-refund','refund-response-lost']:
            report=run_in_process(load_case(case),adapter)
            self.assertEqual(report['verdict'],'UNSUPPORTED')
            self.assertEqual(report['oracle_version'],'1.2')
            self.assertEqual(report['agent']['capabilities'],[])
        self.assertEqual(calls,[])

    def test_declared_supported_capabilities_keep_actual_evaluation(self):
        def adapter(*args): return corrected(*args)
        adapter.capabilities=SupportTools.capabilities
        report=run_in_process(load_case('refund-response-lost'),adapter)
        self.assertEqual(report['verdict'],'PASS')
        self.assertEqual(report['metrics']['refund_count'],1)
