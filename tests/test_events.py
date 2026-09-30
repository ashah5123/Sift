import unittest
from datetime import datetime, timezone

from sift.github.events import parse_event
from sift.jobs import Job
from tests.helpers import issue_payload


class TestParseEvent(unittest.TestCase):
    def test_opened_issue(self):
        job = parse_event("issues", "d1", issue_payload())
        self.assertEqual(job.repo, "owner/repo")
        self.assertEqual(job.number, 7)
        self.assertEqual(job.installation_id, 42)
        self.assertEqual(job.actor, "maintainer")
        self.assertEqual(job.item["labels"], ["bug"])
        self.assertEqual(job.item["author"], "reporter")
        self.assertIsNone(job.label)

    def test_labeled_carries_label(self):
        job = parse_event("issues", "d1", issue_payload(action="labeled", label="bug"))
        self.assertEqual(job.label, "bug")

    def test_pull_request(self):
        payload = issue_payload()
        payload["pull_request"] = payload.pop("issue")
        job = parse_event("pull_request", "d1", payload)
        self.assertTrue(job.to_issue().is_pr)

    def test_ignored(self):
        self.assertIsNone(parse_event("push", "d1", {"action": None}))
        self.assertIsNone(parse_event("issues", "d1", issue_payload(action="assigned")))
        self.assertIsNone(parse_event("issues", "d1", issue_payload(sender_type="Bot")))

    def test_json_roundtrip_and_to_issue(self):
        job = parse_event("issues", "d1", issue_payload())
        again = Job.from_json(job.to_json())
        self.assertEqual(again, job)
        issue = again.to_issue()
        self.assertEqual(issue.title, "Crash on startup")
        self.assertEqual(issue.created_at, datetime(2026, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
