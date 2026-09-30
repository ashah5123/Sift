from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    body: str
    created_at: datetime
    labels: tuple[str, ...] = ()
    is_pr: bool = False
    duplicate_of: int | None = None  # ground truth, backtests only

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.body}"


@dataclass(frozen=True)
class Decision:
    """One answer from Jev plus how sure it is (0..1)."""

    answer: str
    confidence: float
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass
class TriageResult:
    duplicate_of: int | None = None
    labels: list[str] = field(default_factory=list)
    priority: int | None = None
    flags: list[str] = field(default_factory=list)
