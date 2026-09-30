import unittest

from sift.models import Decision
from tests.helpers import make_issue


class TestModels(unittest.TestCase):
    def test_issue_text_joins_title_and_body(self):
        issue = make_issue(title="Crash on start", body="It crashes.")
        self.assertEqual(issue.text, "Crash on start\n\nIt crashes.")

    def test_decision_rejects_out_of_range_confidence(self):
        with self.assertRaises(ValueError):
            Decision("yes", 1.5)
        with self.assertRaises(ValueError):
            Decision("yes", -0.1)


if __name__ == "__main__":
    unittest.main()
