import unittest

from sift.jev import get_jev
from sift.jev.local import MAX_BODY_CHARS, LocalJev, issue_block
from tests.helpers import make_issue


class StubJev(LocalJev):
    """LocalJev with the model replaced by fixed option probabilities."""

    def __init__(self, probs):
        super().__init__()
        self.probs = probs
        self.questions = []

    def _option_probs(self, question, options):
        self.questions.append((question, options))
        return self.probs[: len(options)]


class TestLocalJev(unittest.IsolatedAsyncioTestCase):
    async def test_same_issue_reports_probability_of_yes(self):
        d = await StubJev([0.8, 0.2]).same_issue(make_issue(2, "a"), make_issue(1, "b"))
        self.assertEqual((d.answer, round(d.confidence, 6)), ("yes", 0.8))
        d = await StubJev([0.3, 0.7]).same_issue(make_issue(2, "a"), make_issue(1, "b"))
        self.assertEqual((d.answer, round(d.confidence, 6)), ("no", 0.7))
        self.assertIn("p(yes)=0.300", d.reason)

    async def test_prompt_contains_both_issues_and_options(self):
        jev = StubJev([0.5, 0.5])
        await jev.same_issue(make_issue(2, "Crash on save"), make_issue(1, "Save crashes"))
        question, options = jev.questions[0]
        self.assertIn("Crash on save", question)
        self.assertIn("Save crashes", question)
        self.assertEqual(options, ["Yes", "No"])

    async def test_pick_label_allows_none(self):
        jev = StubJev([0.1, 0.2, 0.7])
        d = await jev.pick_label(make_issue(title="x"), ["bug", "docs"])
        self.assertEqual(d.answer, "none")
        self.assertEqual(jev.questions[0][1], ["bug", "docs", "none"])

    async def test_priority_and_flags(self):
        d = await StubJev([0.6, 0.1, 0.1, 0.1, 0.1]).score_priority(make_issue(), "")
        self.assertEqual(d.answer, "1")
        d = await StubJev([0.9, 0.1]).check_flag(make_issue(), "public_security")
        self.assertEqual(d.answer, "yes")
        with self.assertRaises(ValueError):
            await StubJev([0.5, 0.5]).check_flag(make_issue(), "nope")

    def test_issue_block_strips_attachments_and_truncates(self):
        body = "real\n<details>System Info</details>\n" + "x" * (MAX_BODY_CHARS + 50)
        block = issue_block("Issue", make_issue(5, "Title", body))
        self.assertIn("#5", block)
        self.assertNotIn("System Info", block)
        self.assertTrue(block.endswith("[...]"))

    def test_factory_does_not_load_model(self):
        jev = get_jev("local")
        self.assertIsInstance(jev, LocalJev)
        self.assertIsNone(jev._model)


if __name__ == "__main__":
    unittest.main()
