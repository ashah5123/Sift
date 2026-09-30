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


if __name__ == "__main__":
    unittest.main()
