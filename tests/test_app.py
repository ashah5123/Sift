import json
import unittest

from tests.helpers import has, issue_payload

if has("fastapi", "httpx"):
    from fastapi.testclient import TestClient

    from sift.app import create_app
    from sift.config import Settings
    from sift.github.signature import sign
    from sift.queue import InMemoryQueue

SECRET = "test-secret"


@unittest.skipUnless(has("fastapi", "httpx"), "fastapi/httpx not installed")
class TestWebhook(unittest.TestCase):
    def setUp(self):
        self.queue = InMemoryQueue()
        self.client = TestClient(create_app(Settings(webhook_secret=SECRET), queue=self.queue))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def post(self, payload, event="issues", delivery="d1", secret=SECRET):
        body = json.dumps(payload).encode()
        return self.client.post(
            "/webhook",
            content=body,
            headers={
                "X-GitHub-Event": event,
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": sign(secret, body),
                "Content-Type": "application/json",
            },
        )

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})

    def test_bad_signature_rejected(self):
        r = self.post(issue_payload(), secret="wrong")
        self.assertEqual(r.status_code, 401)

    def test_ping(self):
        self.assertEqual(self.post({"zen": "hi"}, event="ping").json(), {"status": "pong"})

    def test_issue_queued_once(self):
        r = self.post(issue_payload())
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json(), {"status": "queued"})
        self.assertEqual(self.post(issue_payload()).json(), {"status": "duplicate"})

    def test_irrelevant_event_ignored(self):
        self.assertEqual(self.post({"action": None}, event="push").json(), {"status": "ignored"})

    def test_requires_secret(self):
        with self.assertRaises(RuntimeError):
            create_app(Settings(webhook_secret=""))


if __name__ == "__main__":
    unittest.main()
