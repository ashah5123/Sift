import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from sift.jobs import Job


class JobQueue(Protocol):
    async def enqueue(self, job: Job) -> bool:
        """Add a job. Returns False if this delivery id was already enqueued."""
        ...

    async def read(self, consumer: str, count: int = 10, block_ms: int = 5000) -> list[tuple[str, Job]]:
        ...

    async def ack(self, msg_id: str) -> None: ...

    async def retry(self, msg_id: str, job: Job, delay_s: float) -> None:
        """Re-deliver the job after `delay_s` with attempts + 1."""
        ...

    async def dead_letter(self, msg_id: str, job: Job, error: str) -> None: ...


class InMemoryQueue:
    """Same semantics as the Redis queue, for tests and local runs."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._ids = itertools.count(1)
        self._seen: set[str] = set()
        self._ready: deque[tuple[str, Job]] = deque()
        self._delayed: list[tuple[float, Job]] = []
        self.pending: dict[str, Job] = {}
        self.dead: list[tuple[Job, str]] = []

    async def enqueue(self, job: Job) -> bool:
        if job.delivery_id in self._seen:
            return False
        self._seen.add(job.delivery_id)
        self._ready.append((str(next(self._ids)), job))
        return True

    async def read(self, consumer: str, count: int = 10, block_ms: int = 0) -> list[tuple[str, Job]]:
        now = self._clock()
        still_delayed = []
        for due_at, job in self._delayed:
            if due_at <= now:
                self._ready.append((str(next(self._ids)), job))
            else:
                still_delayed.append((due_at, job))
        self._delayed = still_delayed

        out = []
        while self._ready and len(out) < count:
            msg_id, job = self._ready.popleft()
            self.pending[msg_id] = job
            out.append((msg_id, job))
        return out

    async def ack(self, msg_id: str) -> None:
        self.pending.pop(msg_id, None)

    async def retry(self, msg_id: str, job: Job, delay_s: float) -> None:
        self.pending.pop(msg_id, None)
        self._delayed.append((self._clock() + delay_s, replace(job, attempts=job.attempts + 1)))

    async def dead_letter(self, msg_id: str, job: Job, error: str) -> None:
        self.pending.pop(msg_id, None)
        self.dead.append((job, error))
