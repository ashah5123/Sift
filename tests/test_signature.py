import unittest

from sift.github.signature import sign, verify_signature


class TestSignature(unittest.TestCase):
    body = b'{"action":"opened"}'

    def test_valid(self):
        self.assertTrue(verify_signature("s3cret", self.body, sign("s3cret", self.body)))

    def test_wrong_secret(self):
        self.assertFalse(verify_signature("s3cret", self.body, sign("other", self.body)))

    def test_tampered_body(self):
        self.assertFalse(verify_signature("s3cret", self.body + b" ", sign("s3cret", self.body)))

    def test_missing_or_malformed_header(self):
        self.assertFalse(verify_signature("s3cret", self.body, None))
        self.assertFalse(verify_signature("s3cret", self.body, "sha1=abc"))

    def test_empty_secret_never_verifies(self):
        self.assertFalse(verify_signature("", self.body, sign("", self.body)))


if __name__ == "__main__":
    unittest.main()
