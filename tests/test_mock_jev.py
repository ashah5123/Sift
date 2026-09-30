import unittest

from sift.jev import FLAGS, MockJev, get_jev
from sift.similarity import jaccard
from tests.helpers import make_issue


class TestSimilarity(unittest.TestCase):
    def test_jaccard(self):
        self.assertEqual(jaccard("foo bar baz", "foo bar baz"), 1.0)
        self.assertEqual(jaccard("foo bar", "qux quux"), 0.0)
        self.assertEqual(jaccard("", "anything"), 0.0)


class TestMockJev(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.jev = MockJev()

    async def test_same_issue(self):
        a = make_issue(1, "App crashes when opening settings page", "Segfault in settings module")
        b = make_issue(2, "App crashes when opening settings page", "Segfault in settings module")
        c = make_issue(3, "Add dark mode", "Please support a dark theme")
        self.assertEqual((await self.jev.same_issue(a, b)).answer, "yes")
        self.assertEqual((await self.jev.same_issue(a, c)).answer, "no")

    async def test_pick_label_uses_repo_labels_only(self):
        issue = make_issue(title="Docs typo in README", body="documentation fix")
        d = await self.jev.pick_label(issue, ["bug", "docs", "enhancement"])
        self.assertEqual(d.answer, "docs")
        d = await self.jev.pick_label(make_issue(title="hello"), ["bug"])
        self.assertEqual(d.answer, "none")

    async def test_priority_is_valid(self):
        d = await self.jev.score_priority(make_issue(body="RCE via crafted input"), rubric="")
        self.assertIn(d.answer, {"1", "2", "3", "4", "5"})
        self.assertEqual(d.answer, "1")

    async def test_flags(self):
        sec = make_issue(title="SQL injection in login form", body="payload: ' OR 1=1")
        self.assertEqual((await self.jev.check_flag(sec, "public_security")).answer, "yes")

        bare = make_issue(title="It is broken", body="doesn't work")
        self.assertEqual((await self.jev.check_flag(bare, "missing_repro")).answer, "yes")
        good = make_issue(body="Steps to reproduce:\n1. run it\nExpected behavior: works")
        self.assertEqual((await self.jev.check_flag(good, "missing_repro")).answer, "no")

        slop = make_issue(body="Certainly! Let us delve into this issue. I hope this helps.")
        self.assertEqual((await self.jev.check_flag(slop, "ai_slop")).answer, "yes")

    async def test_every_flag_supported(self):
        for flag in FLAGS:
            d = await self.jev.check_flag(make_issue(body="x"), flag)
            self.assertIn(d.answer, {"yes", "no"})


class TestFactory(unittest.TestCase):
    def test_default_is_mock(self):
        self.assertIsInstance(get_jev("mock"), MockJev)

    def test_unknown_backend(self):
        with self.assertRaises(NotImplementedError):
            get_jev("real")


if __name__ == "__main__":
    unittest.main()
