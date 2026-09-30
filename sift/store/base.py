from typing import Any, Protocol

from sift.jobs import Job, parse_ts


class IssueStore(Protocol):
    async def apply(self, job: Job) -> bool:
        """Record the event and upsert the issue in one step.

        Returns False if this delivery was already applied, so retries are safe.
        An older event never overwrites a newer version of the issue.
        """
        ...


class InMemoryStore:
    def __init__(self) -> None:
        self.events: dict[str, Job] = {}
        self.issues: dict[tuple[str, int], dict[str, Any]] = {}

    async def apply(self, job: Job) -> bool:
        if job.delivery_id in self.events:
            return False
        self.events[job.delivery_id] = job
        key = (job.repo, job.number)
        current = self.issues.get(key)
        incoming = parse_ts(job.item.get("updated_at") or job.item["created_at"])
        if current is None or current["_updated"] <= incoming:
            self.issues[key] = {**job.item, "is_pr": job.event == "pull_request", "_updated": incoming}
        return True
