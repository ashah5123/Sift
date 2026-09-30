import tempfile
import unittest
from pathlib import Path

from sift.config import Settings


class TestSettings(unittest.TestCase):
    def test_inline_key_unescapes_newlines(self):
        s = Settings.from_env({"GITHUB_PRIVATE_KEY": "-----BEGIN-----\\nabc\\n-----END-----"})
        self.assertEqual(s.private_key, "-----BEGIN-----\nabc\n-----END-----")

    def test_key_from_path(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "key.pem"
            path.write_text("PEM")
            s = Settings.from_env({"GITHUB_PRIVATE_KEY_PATH": str(path)})
        self.assertEqual(s.private_key, "PEM")

    def test_defaults(self):
        s = Settings.from_env({})
        self.assertEqual(s.jev_backend, "mock")
        self.assertEqual(s.webhook_secret, "")
        self.assertFalse(s.run_worker)

    def test_run_worker_flag(self):
        self.assertTrue(Settings.from_env({"RUN_WORKER": "1"}).run_worker)
        self.assertTrue(Settings.from_env({"RUN_WORKER": "true"}).run_worker)
        self.assertFalse(Settings.from_env({"RUN_WORKER": "0"}).run_worker)


class TestCleanDsn(unittest.TestCase):
    def test_strips_channel_binding_keeps_sslmode(self):
        from sift.db import clean_dsn

        dsn = "postgresql://u:p@host.neon.tech/neondb?sslmode=require&channel_binding=require"
        self.assertEqual(clean_dsn(dsn), "postgresql://u:p@host.neon.tech/neondb?sslmode=require")

    def test_no_query_unchanged(self):
        from sift.db import clean_dsn

        self.assertEqual(clean_dsn("postgresql://u:p@h/db"), "postgresql://u:p@h/db")


if __name__ == "__main__":
    unittest.main()
