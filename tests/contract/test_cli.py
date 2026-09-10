from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CLITest(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "breakroom", *map(str, args)], capture_output=True, text=True, timeout=30)

    def test_doctor_and_invalid_configuration(self):
        self.assertEqual(self.cli("doctor").returncode, 0)
        self.assertEqual(self.cli("run", "--agent", "bad").returncode, 2)
        self.assertEqual(self.cli("validate-pack", "scenario-packs/support-refunds").returncode, 0)

    def test_documented_end_to_end_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            demo = self.cli("demo", "--output", root / "demo")
            self.assertEqual(demo.returncode, 0, demo.stderr + demo.stdout)
            self.assertIn("faulty agent FAILS", demo.stdout)
            bad = self.cli("run", "--agent", "examples.agents.faulty:run", "--pack", "support-refunds", "--out", root / "baseline")
            good = self.cli("run", "--agent", "examples.agents.corrected:run", "--pack", "support-refunds", "--out", root / "candidate")
            self.assertEqual(bad.returncode, 1, bad.stderr + bad.stdout)
            self.assertEqual(good.returncode, 0, good.stderr + good.stdout)
            compare = self.cli("compare", root / "baseline", root / "candidate")
            self.assertEqual(compare.returncode, 0, compare.stderr + compare.stdout)
            export = self.cli("export-case", root / "baseline", "--case", "refund-response-lost", "--out", root / "regressions")
            self.assertEqual(export.returncode, 0, export.stderr)
            for name in ("report.json", "report.html", "junit.xml"):
                self.assertTrue((root / "candidate" / name).exists())
            self.assertTrue((root / "regressions/test_regression.py").exists())


if __name__ == "__main__":
    unittest.main()
