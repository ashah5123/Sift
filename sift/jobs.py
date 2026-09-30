import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sift.models import Issue


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Job:
    """One webhook delivery, trimmed to what the worker needs."""

    delivery_id: str
    event: str                 # "issues" | "pull_request"
    action: str                # "opened", "labeled", ...
    repo: str                  # "owner/name"
    number: int
    installation_id: int | None
    actor: str                 # who triggered the event
    item: dict[str, Any]       # trimmed issue / PR object
    label: str | None = None   # for labeled / unlabeled
    attempts: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Job":
        return cls(**json.loads(raw))

    def to_issue(self) -> Issue:
        return Issue(
            repo=self.repo,
            number=self.number,
            title=self.item.get("title") or "",
            body=self.item.get("body") or "",
            created_at=parse_ts(self.item["created_at"]),
            labels=tuple(self.item.get("labels") or ()),
            is_pr=self.event == "pull_request",
        )
