"""Historical issues with ground truth, stored as JSONL (one issue per line)."""
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sift.models import Issue

DATA_DIR = Path("data")

# Labels that describe process state rather than what the issue is about.
# They are excluded from label ground truth.
PROCESS_LABEL = re.compile(
    r"dup|stale|invalid|wont.?fix|won't fix|answered|triage|needs|awaiting|info.?needed|"
    r"more.?info|locked|resolution|closed|pending|waiting|reviewed|migrate|status|unconfirmed|confirmed|"
    r"released|release|verif|testplan",
    re.I,
)


@dataclass(frozen=True)
class Record:
    issue: Issue
    is_duplicate: bool
    closed_at: datetime | None = None

    @property
    def duplicate_of(self) -> int | None:
        return self.issue.duplicate_of

    @property
    def topic_labels(self) -> tuple[str, ...]:
        return tuple(label for label in self.issue.labels if not PROCESS_LABEL.search(label))


def dataset_path(repo: str) -> Path:
    return DATA_DIR / (repo.replace("/", "__") + ".jsonl")


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def record_from_row(row: dict) -> Record:
    issue = Issue(
        repo=row["repo"],
        number=row["number"],
        title=row.get("title") or "",
        body=row.get("body") or "",
        created_at=_ts(row["created_at"]),
        labels=tuple(row.get("labels") or ()),
        duplicate_of=row.get("duplicate_of"),
    )
    return Record(issue=issue, is_duplicate=bool(row.get("is_duplicate")), closed_at=_ts(row.get("closed_at")))


def load(path: Path) -> list[Record]:
    with path.open() as f:
        records = [record_from_row(json.loads(line)) for line in f if line.strip()]
    return sorted(records, key=lambda r: (r.issue.created_at, r.issue.number))


def save(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["created_at"], r["number"]))
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
