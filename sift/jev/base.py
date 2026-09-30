from typing import Literal, Protocol

from sift.models import Decision, Issue

Flag = Literal["ai_slop", "missing_repro", "public_security"]
FLAGS: tuple[Flag, ...] = ("ai_slop", "missing_repro", "public_security")


class JevClient(Protocol):
    """Every question Sift asks Jev. Implementations are selected by JEV_BACKEND."""

    async def same_issue(self, new: Issue, candidate: Issue) -> Decision:
        """Is `new` the same bug as `candidate`? answer: 'yes' | 'no'."""
        ...

    async def pick_label(self, issue: Issue, labels: list[str]) -> Decision:
        """Best label from the repo's own list. answer: one of `labels` | 'none'."""
        ...

    async def score_priority(self, issue: Issue, rubric: str) -> Decision:
        """Score against the maintainer's rubric. answer: '1'..'5' (1 = most urgent)."""
        ...

    async def check_flag(self, issue: Issue, flag: Flag) -> Decision:
        """Does the issue have this problem? answer: 'yes' | 'no'."""
        ...
