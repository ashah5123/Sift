import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

from sift.backtest import dataset, labeling
from sift.backtest.detectors import (
    Boilerplate,
    JaccardDetector,
    JevDetector,
    TfIdfDetector,
    TfIdfIndex,
    TokenIndex,
)
from sift.backtest.loader import (
    GitHubREST,
    duplicate_target,
    needs_comment_check,
    next_link,
    row_from_api,
)
from sift.backtest.replay import DupOutcome, ReplayResult, duplicate_metrics, label_metrics, replay, summary
from sift.backtest.report import format_report
from sift.jev.mock import MockJev
from sift.models import Issue

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def rec(number, title, body="", *, dup_of=None, is_dup=None, labels=(), day=None):
    issue = Issue(
        repo="o/r", number=number, title=title, body=body,
        created_at=T0 + timedelta(days=number if day is None else day),
        labels=tuple(labels), duplicate_of=dup_of,
    )
    return dataset.Record(issue=issue, is_duplicate=bool(dup_of) if is_dup is None else is_dup)


class TestLoaderParsing(unittest.TestCase):
    def test_duplicate_target(self):
        self.assertEqual(duplicate_target("Duplicate of #123", "o/r", 5), 123)
        self.assertEqual(duplicate_target("dupe of #9", "o/r", 5), 9)
        self.assertEqual(
            duplicate_target("duplicate of https://github.com/o/r/issues/77 thanks", "o/r", 5), 77
        )
        self.assertIsNone(duplicate_target("duplicate of https://github.com/x/y/issues/77", "o/r", 5))
        self.assertIsNone(duplicate_target("see #12", "o/r", 5))
        self.assertIsNone(duplicate_target("Duplicate of #5", "o/r", 5))  # self-reference

    def test_row_from_api(self):
        item = {
            "number": 3, "title": "t", "body": None, "created_at": "2024-01-01T00:00:00Z",
            "closed_at": None, "state": "open", "state_reason": None,
            "labels": [{"name": "*duplicate"}, {"name": "bug"}], "user": {"login": "u"},
        }
        row = row_from_api("o/r", item)
        self.assertTrue(row["is_duplicate"])
        self.assertEqual(row["body"], "")
        self.assertTrue(needs_comment_check(row))
        item.update(labels=[], state_reason="completed")
        self.assertFalse(needs_comment_check(row_from_api("o/r", item)))
        item.update(state_reason="not_planned")
        self.assertTrue(needs_comment_check(row_from_api("o/r", item)))

    def test_next_link(self):
        header = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"'
        self.assertEqual(next_link(header), "https://api.github.com/x?page=2")
        self.assertIsNone(next_link('<https://api.github.com/x?page=1>; rel="prev"'))
        self.assertIsNone(next_link(""))


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestGitHubREST(unittest.TestCase):
    def test_retries_rate_limit_then_paginates(self):
        calls = []

        def opener(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                hdrs = Message()
                hdrs["Retry-After"] = "3"
                raise urllib.error.HTTPError(req.full_url, 429, "slow down", hdrs, io.BytesIO(b""))
            if "page=2" in req.full_url:
                return FakeResponse([{"n": 2}])
            return FakeResponse([{"n": 1}], {"Link": '<https://api.github.com/x?page=2>; rel="next"'})

        slept = []
        api = GitHubREST("tok", opener=opener, sleep=slept.append)
        pages = list(api.paginate("/x", {"per_page": 1}))
        self.assertEqual(pages, [[{"n": 1}], [{"n": 2}]])
        self.assertEqual(slept, [3.0])
        self.assertEqual(len(calls), 3)


class TestDataset(unittest.TestCase):
    def test_roundtrip_sorted_and_topic_labels(self):
        rows = [
            {"repo": "o/r", "number": 2, "title": "b", "body": "", "created_at": "2024-01-02T00:00:00Z",
             "labels": ["bug", "stale"], "is_duplicate": False, "duplicate_of": None},
            {"repo": "o/r", "number": 1, "title": "a", "body": "", "created_at": "2024-01-01T00:00:00Z",
             "labels": ["duplicate"], "is_duplicate": True, "duplicate_of": 0},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "o__r.jsonl"
            dataset.save(rows, path)
            records = dataset.load(path)
        self.assertEqual([r.issue.number for r in records], [1, 2])
        self.assertEqual(records[1].topic_labels, ("bug",))
        self.assertEqual(records[0].topic_labels, ())
        self.assertEqual(records[0].duplicate_of, 0)


class TestTokenIndex(unittest.TestCase):
    def test_search_ranks_by_overlap(self):
        index = TokenIndex()
        index.add(rec(1, "websocket disconnects under load", "uvicorn websocket timeout").issue)
        index.add(rec(2, "docs typo in tutorial", "tutorial page spelling").issue)
        results = index.search(rec(3, "websocket timeout under load").issue, k=5)
        self.assertEqual(results[0][0].number, 1)
        self.assertTrue(all(n.number != 2 for n, _ in results))


class TestTfIdf(unittest.TestCase):
    def test_template_lines_are_learned_and_stripped(self):
        template = "Describe what you were doing when the bug occurred\nPlease do not remove the text below"
        index = TfIdfIndex()
        for n, topic in enumerate(["router crash", "hydration mismatch", "suspense hang"], start=1):
            index.add(rec(n, topic, f"{template}\n{topic} details here").issue)
        cleaned = index.boilerplate.clean(rec(9, "Title", f"{template}\nreal content").issue)
        self.assertNotIn("describe what you were doing", cleaned.lower())
        self.assertIn("real content", cleaned)

    def test_html_comments_stripped(self):
        cleaned = Boilerplate().clean(rec(1, "T", "<!-- fill this in\nplease -->\nactual text").issue)
        self.assertNotIn("fill this in", cleaned)
        self.assertIn("actual text", cleaned)

    def test_empty_template_issue_is_not_indexed_or_queried(self):
        index = TfIdfIndex()
        index.add(rec(1, "Bug:", "<!-- template -->").issue)
        index.add(rec(2, "Websocket reconnect loop", "reconnect loop after server restart").issue)
        self.assertEqual(index.search(rec(3, "Bug:", "").issue, 5), [])
        numbers = [c.number for c, _ in index.search(rec(4, "websocket reconnect loop restart", "").issue, 5)]
        self.assertEqual(numbers, [2])

    def test_early_short_issue_does_not_match_everything(self):
        # Regression: norms frozen at insert time inflated scores of early issues.
        index = TfIdfIndex()
        index.add(rec(1, "Unable to add workspace folder", "workspace folder add fails").issue)
        for n in range(2, 300):
            index.add(rec(n, f"topic{n} alpha{n} beta{n}", f"gamma{n} delta{n} workspace").issue)
        results = index.search(rec(400, "Chat slow to start", "chat startup takes long workspace").issue, 3)
        self.assertTrue(all(score < 0.5 for _, score in results), results)

    def test_ranks_specific_match_first(self):
        index = TfIdfIndex()
        index.add(rec(1, "Cannot read property isCollapsed of undefined", "devtools profiler crash").issue)
        index.add(rec(2, "Profiler shows wrong commit durations", "devtools profiler timing").issue)
        index.add(rec(3, "Docs typo", "tutorial spelling fix").issue)
        top = index.search(rec(4, "isCollapsed undefined error", "devtools crashes on isCollapsed").issue, 3)
        self.assertEqual(top[0][0].number, 1)


class SpyDetector(JaccardDetector):
    """Records the pool size at each query to prove no future leakage."""

    name = "spy"

    def __init__(self):
        super().__init__()
        self.seen_at_query = []

    async def candidates(self, issue, k):
        self.seen_at_query.append((issue.number, len(self.index), issue.labels, issue.duplicate_of))
        return await super().candidates(issue, k)


class TestReplay(unittest.IsolatedAsyncioTestCase):
    def records(self):
        return [
            rec(1, "Crash when uploading large files", "uploading a 2GB file crashes the server",
                labels=["bug"]),
            rec(2, "Add dark mode to docs site", "docs site needs a dark theme", labels=["docs"]),
            rec(3, "Server crashes uploading large files", "crashes when uploading a 2GB file",
                dup_of=1, labels=["bug", "duplicate"]),
            rec(4, "Unrelated feature request", "support yaml config files", labels=["enhancement"]),
            rec(5, "Some dup with unknown original", "text", is_dup=True),
        ]

    async def test_no_future_leakage(self):
        spy = SpyDetector()
        await replay(self.records(), spy)
        # Issue n is queried when exactly n-1 earlier issues are in the pool,
        # with labels and duplicate links hidden.
        for number, pool_size, labels, dup in spy.seen_at_query:
            self.assertEqual(pool_size, number - 1)
            self.assertEqual(labels, ())
            self.assertIsNone(dup)

    async def test_duplicate_found_and_metrics(self):
        result = await replay(self.records(), JaccardDetector(), label_jev=MockJev())
        self.assertEqual(result.evaluated, 5)
        self.assertEqual(result.unverifiable, 1)
        by_number = {o.number: o for o in result.dups}
        self.assertEqual(by_number[3].target, 1)
        self.assertEqual(by_number[3].best, 1)
        self.assertTrue(by_number[3].target_in_candidates)

        m = duplicate_metrics(result)
        self.assertEqual(m["known_duplicates"], 1)
        self.assertEqual(m["recall_at_10"], 1.0)
        low = m["sweep"][0]
        self.assertEqual(low["recall"], 1.0)
        self.assertGreaterEqual(low["flagged"], 1)

    async def test_tfidf_detector_finds_duplicate(self):
        result = await replay(self.records(), TfIdfDetector())
        by_number = {o.number: o for o in result.dups}
        self.assertEqual(by_number[3].best, 1)

    async def test_eval_last_scores_only_tail(self):
        result = await replay(self.records(), JaccardDetector(), eval_last=2)
        self.assertEqual(result.evaluated, 2)

    async def test_labels_only_use_past_vocabulary(self):
        result = await replay(self.records(), JaccardDetector(), label_jev=MockJev())
        m = label_metrics(result)
        self.assertEqual(m["labeled_issues"], 4)  # "duplicate" is a process label; #5 has none
        self.assertEqual(result.labels[0].predicted, "none")  # nothing known before issue 1
        self.assertIsNone(result.labels[0].baseline)

    async def test_jev_detector_counts_tokens_and_report_renders(self):
        det = JevDetector(MockJev(), name="mock-jev")
        result = await replay(self.records(), det, label_jev=MockJev())
        self.assertGreater(result.tokens_used, 0)
        s = summary(result)
        self.assertGreater(s["est_cost_per_1k_issues_usd"], 0)
        text = format_report("o/r", s)
        self.assertIn("precision", text)
        self.assertIn("mock-jev", text)


class TestLabeling(unittest.TestCase):
    def result(self):
        r = ReplayResult(detector="tfidf", k=10)
        r.dups = [
            DupOutcome(10, target=1, best=1, confidence=0.9, target_in_candidates=True),   # correct
            DupOutcome(11, target=None, best=2, confidence=0.8, target_in_candidates=False),  # wrong
            DupOutcome(12, target=None, best=3, confidence=0.6, target_in_candidates=False),  # wrong
            DupOutcome(13, target=4, best=5, confidence=0.7, target_in_candidates=False),     # wrong original
            DupOutcome(14, target=None, best=6, confidence=0.3, target_in_candidates=False),  # below threshold
            DupOutcome(15, target=None, best=None, confidence=0.0, target_in_candidates=False),
        ]
        return r

    def records(self):
        return [rec(n, f"title {n}", f"body {n}") for n in range(1, 16)]

    def test_sample_only_wrong_flags_above_threshold_and_seeded(self):
        labels = labeling.build_sample("o/r", self.records(), self.result(), threshold=0.5, n=10)
        self.assertEqual((labels["flagged"], labels["marked_correct"], labels["wrong_flags"]), (4, 1, 3))
        self.assertEqual([row["issue"] for row in labels["sample"]], [11, 12, 13])
        self.assertEqual(labels["sample"][2]["marked_original"], 4)
        self.assertEqual(labels["sample"][0]["candidate_title"], "title 2")
        self.assertTrue(all(row["verdict"] is None for row in labels["sample"]))

        a = labeling.build_sample("o/r", self.records(), self.result(), 0.5, n=2, seed=7)
        b = labeling.build_sample("o/r", self.records(), self.result(), 0.5, n=2, seed=7)
        self.assertEqual(a, b)
        self.assertEqual(len(a["sample"]), 2)

    def test_wilson(self):
        lo, hi = labeling.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        self.assertEqual(labeling.wilson(0, 0), (0.0, 1.0))
        self.assertEqual(labeling.wilson(10, 10)[1], 1.0)

    def test_estimate_corrects_precision_with_labeled_share(self):
        labels = {
            "flagged": 100, "marked_correct": 10, "wrong_flags": 90,
            "sample": [{"verdict": v} for v in ["yes"] * 3 + ["no"] * 6 + ["unsure", None]],
        }
        e = labeling.estimate(labels)
        self.assertEqual((e["labeled"], e["unsure"], e["unlabeled"]), (9, 1, 1))
        self.assertAlmostEqual(e["measured_precision"], 0.10)
        self.assertAlmostEqual(e["estimated_precision"], (10 + 90 / 3) / 100)
        lo, hi = e["estimated_precision_95ci"]
        self.assertLess(lo, e["estimated_precision"])
        self.assertGreater(hi, e["estimated_precision"])

    def test_estimate_without_verdicts(self):
        e = labeling.estimate({"flagged": 4, "marked_correct": 1, "wrong_flags": 3, "sample": [{"verdict": None}]})
        self.assertIsNone(e["estimated_precision"])
        self.assertAlmostEqual(e["measured_precision"], 0.25)

    def test_interactive_labeling_saves_and_resumes(self):
        labels = labeling.build_sample("o/r", self.records(), self.result(), 0.5, n=10)
        issues = {r.issue.number: r.issue for r in self.records()}
        saves, shown = [], []
        answers = iter(["maybe", "y", "s", "q"])  # invalid answer is asked again
        n = labeling.label_interactively(
            labels, issues, lambda l: saves.append([row["verdict"] for row in l["sample"]]),
            ask=lambda _: next(answers), out=shown.append,
        )
        self.assertEqual(n, 1)
        self.assertEqual(saves, [["yes", None, None]])
        self.assertTrue(any("https://github.com/o/r/issues/2" in line for line in shown))

        answers = iter(["n", "u"])  # resumes with the two unlabeled pairs
        labeling.label_interactively(labels, issues, lambda l: None, ask=lambda _: next(answers), out=lambda _: None)
        self.assertEqual([row["verdict"] for row in labels["sample"]], ["yes", "no", "unsure"])

    def test_save_load_roundtrip(self):
        labels = labeling.build_sample("o/r", self.records(), self.result(), 0.5, n=10)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.json"
            labeling.save(labels, path)
            self.assertEqual(labeling.load(path), labels)

    def test_excerpt_strips_template_comments(self):
        issue = rec(1, "t", "<!-- instructions -->\n\n\nreal text\n\n\nmore").issue
        self.assertEqual(labeling.excerpt(issue), "real text\nmore")
        self.assertTrue(labeling.excerpt(rec(1, "t", "x" * 900).issue).endswith("[...]"))


if __name__ == "__main__":
    unittest.main()
