"""Read-only guards for the script's destructive-operation scope."""
import importlib.util
from pathlib import Path
import unittest
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/backup-restore-smoke.py"
spec = importlib.util.spec_from_file_location("breakroom_backup_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class BackupScopeTest(unittest.TestCase):
    def test_driver_query_cannot_override_disposable_database_or_host(self):
        base = "postgresql+psycopg://breakroom:local-password@127.0.0.1:54329/breakroom"
        for query in ("dbname=breakroom", "database=breakroom", "host=remote.example", "port=5432", "options=-csearch_path=other", "sslmode=disable"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                smoke.local_database_location(base + "?" + query)

    def test_driver_resolves_only_the_generated_disposable_target(self):
        location = smoke.local_database_location("postgresql+psycopg://breakroom:local-password@127.0.0.1:54329/breakroom")
        database = "br_backup_smoke_" + "a" * 32 + "_source"
        _, actual = PGDialect_psycopg().create_connect_args(location.set(database=database))
        self.assertEqual(actual["dbname"], database)
        self.assertEqual(actual["host"], "127.0.0.1")
        self.assertEqual(actual["port"], 54329)
        self.assertEqual(actual["user"], "breakroom")

    def test_remote_hosts_ports_roles_and_drivers_are_rejected(self):
        for url in ("postgresql+psycopg://breakroom:p@remote.example:54329/breakroom",
                    "postgresql+psycopg://breakroom:p@127.0.0.1:5432/breakroom",
                    "postgresql+psycopg://another:p@127.0.0.1:54329/breakroom",
                    "postgresql://breakroom:p@127.0.0.1:54329/breakroom"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                smoke.local_database_location(url)


if __name__ == "__main__":
    unittest.main()
