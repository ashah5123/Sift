from typing import Any

from sift.jobs import Job

_ACTIONS = {"opened", "edited", "reopened", "closed", "labeled", "unlabeled"}
HANDLED = {"issues": _ACTIONS, "pull_request": _ACTIONS}
ITEM_KEYS = ("title", "body", "state", "state_reason", "created_at", "updated_at")


def parse_event(event: str, delivery_id: str, payload: dict[str, Any]) -> Job | None:
    """Turn a webhook payload into a Job, or None if Sift doesn't care about it."""
    action = payload.get("action")
    if action not in HANDLED.get(event, ()):
        return None
    sender = payload.get("sender") or {}
    # Skip bots, including Sift itself: its own label changes must not come back as
    # "maintainer corrections", and this also avoids feedback loops.
    if sender.get("type") == "Bot":
        return None

    raw = payload["issue" if event == "issues" else "pull_request"]
    item = {key: raw.get(key) for key in ITEM_KEYS}
    item["labels"] = [label["name"] for label in raw.get("labels") or []]
    item["author"] = (raw.get("user") or {}).get("login")

    return Job(
        delivery_id=delivery_id,
        event=event,
        action=action,
        repo=payload["repository"]["full_name"],
        number=raw["number"],
        installation_id=(payload.get("installation") or {}).get("id"),
        actor=sender.get("login", ""),
        item=item,
        label=(payload.get("label") or {}).get("name"),
    )
