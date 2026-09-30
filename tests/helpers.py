import importlib.util
import os
from datetime import datetime, timezone

from sift.jobs import Job
from sift.models import Issue


def has(*modules: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in modules)


REDIS_URL = os.environ.get("SIFT_TEST_REDIS_URL")
DATABASE_URL = os.environ.get("SIFT_TEST_DATABASE_URL")


def make_issue(number: int = 1, title: str = "", body: str = "", **kwargs) -> Issue:
    return Issue(
        repo="owner/repo",
        number=number,
        title=title,
        body=body,
        created_at=kwargs.pop("created_at", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        **kwargs,
    )


def issue_payload(
    action: str = "opened",
    number: int = 7,
    title: str = "Crash on startup",
    body: str = "Steps to reproduce: run it",
    updated_at: str = "2026-01-01T00:00:00Z",
    sender_type: str = "User",
    label: str | None = None,
) -> dict:
    payload = {
        "action": action,
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "state_reason": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": updated_at,
            "labels": [{"name": "bug"}],
            "user": {"login": "reporter"},
        },
        "repository": {"full_name": "owner/repo"},
        "installation": {"id": 42},
        "sender": {"login": "maintainer", "type": sender_type},
    }
    if label:
        payload["label"] = {"name": label}
    return payload


def make_job(delivery_id: str = "d1", repo: str = "owner/repo", **kwargs) -> Job:
    item = {
        "title": "Crash on startup",
        "body": "details",
        "state": "open",
        "state_reason": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": kwargs.pop("updated_at", "2026-01-01T00:00:00Z"),
        "labels": ["bug"],
        "author": "reporter",
    }
    item.update(kwargs.pop("item", {}))
    defaults = dict(
        delivery_id=delivery_id, event="issues", action="opened", repo=repo, number=7,
        installation_id=42, actor="maintainer", item=item,
    )
    defaults.update(kwargs)
    return Job(**defaults)
