from datetime import datetime, timezone

from sift.models import Issue


def make_issue(number: int = 1, title: str = "", body: str = "", **kwargs) -> Issue:
    return Issue(
        repo="owner/repo",
        number=number,
        title=title,
        body=body,
        created_at=kwargs.pop("created_at", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        **kwargs,
    )
