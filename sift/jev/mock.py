"""Rule-based stand-in for Jev so the pipeline runs before API access.

Deterministic on purpose: the same input always gives the same Decision,
which keeps tests and backtests reproducible.
"""
import re

from sift.jev.base import Flag
from sift.models import Decision, Issue
from sift.similarity import jaccard

_SECURITY = re.compile(
    r"\b(cve-\d+|vulnerab\w*|exploit\w*|rce|xss|csrf|sql injection|remote code execution)\b",
    re.I,
)
_REPRO = re.compile(r"(steps to reproduce|to reproduce|repro|expected behavio|actual behavio|```)", re.I)
_SLOP = re.compile(
    r"(as an ai|i hope this helps|certainly! |in conclusion,|delve into|it is important to note)",
    re.I,
)

SAME_ISSUE_THRESHOLD = 0.5


class MockJev:
    async def same_issue(self, new: Issue, candidate: Issue) -> Decision:
        score = jaccard(new.text, candidate.text)
        answer = "yes" if score >= SAME_ISSUE_THRESHOLD else "no"
        # Distance from the threshold stands in for confidence.
        confidence = min(1.0, 0.5 + abs(score - SAME_ISSUE_THRESHOLD))
        return Decision(answer, confidence, f"jaccard={score:.2f}")

    async def pick_label(self, issue: Issue, labels: list[str]) -> Decision:
        text = issue.text.lower()
        for label in labels:
            if re.search(rf"\b{re.escape(label.lower())}\b", text):
                return Decision(label, 0.8, "label name appears in issue")
        return Decision("none", 0.5, "no label matched")

    async def score_priority(self, issue: Issue, rubric: str) -> Decision:
        if _SECURITY.search(issue.text):
            return Decision("1", 0.7, "security keywords")
        return Decision("3", 0.4, "default")

    async def check_flag(self, issue: Issue, flag: Flag) -> Decision:
        if flag == "public_security":
            hit = bool(_SECURITY.search(issue.text))
        elif flag == "missing_repro":
            hit = not issue.is_pr and not _REPRO.search(issue.body)
        elif flag == "ai_slop":
            hit = bool(_SLOP.search(issue.body))
        else:
            raise ValueError(f"unknown flag: {flag}")
        return Decision("yes" if hit else "no", 0.7, "pattern match")
