import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sift.queue import InMemoryQueue
from sift.store import InMemoryStore
from sift.worker import MAX_ATTEMPTS, backoff, run_once, worker_loop
from tests.helpers import make_job


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.queue = InMemoryQueue(clock=self.clock)
        self.store = InMemoryStore()

    async def test_duplicate_delivery_enqueued_once(self):
        self.assertTrue(await self.queue.enqueue(make_job("d1")))
        self.assertFalse(await self.queue.enqueue(make_job("d1")))

    async def test_stores_issue_and_acks(self):
        await self.queue.enqueue(make_job("d1"))
        self.assertEqual(await run_once(self.queue, self.store, "c1"), 1)
        self.assertIn(("owner/repo", 7), self.store.issues)
        self.assertEqual(self.queue.pending, {})

    async def test_reapplying_same_delivery_is_noop(self):
        job = make_job("d1")
        self.assertTrue(await self.store.apply(job))
        self.assertFalse(await self.store.apply(job))

    async def test_older_event_does_not_overwrite_newer(self):
        await self.store.apply(make_job("d2", updated_at="2026-01-02T00:00:00Z", item={"title": "new"}))
        await self.store.apply(make_job("d1", updated_at="2026-01-01T00:00:00Z", item={"title": "old"}))
        self.assertEqual(self.store.issues[("owner/repo", 7)]["title"], "new")

    async def test_failure_retries_with_backoff(self):
        calls = []

        async def flaky(job, store):
            calls.append(job.attempts)
            if len(calls) == 1:
                raise RuntimeError("boom")
            await store.apply(job)

        await self.queue.enqueue(make_job("d1"))
        await run_once(self.queue, self.store, "c1", handler=flaky)
        self.assertEqual(await run_once(self.queue, self.store, "c1", handler=flaky), 0)  # not due yet

        self.clock.now += backoff(0)
        await run_once(self.queue, self.store, "c1", handler=flaky)
        self.assertEqual(calls, [0, 1])
        self.assertIn(("owner/repo", 7), self.store.issues)

    async def test_dead_letter_after_max_attempts(self):
        async def always_fails(job, store):
            raise RuntimeError("boom")

        await self.queue.enqueue(make_job("d1"))
        for _ in range(MAX_ATTEMPTS):
            await run_once(self.queue, self.store, "c1", handler=always_fails)
            self.clock.now += 1000
        self.assertEqual(len(self.queue.dead), 1)
        job, error = self.queue.dead[0]
        self.assertEqual(job.attempts, MAX_ATTEMPTS - 1)
        self.assertIn("boom", error)

    async def test_worker_loop_processes_until_stopped(self):
        stop = asyncio.Event()
        await self.queue.enqueue(make_job("d1"))

        async def handler_then_stop(job, store):
            await store.apply(job)
            stop.set()

        async def run_once_patched(*args, **kwargs):
            return await run_once(*args, handler=handler_then_stop, **kwargs)

        with patch("sift.worker.run_once", run_once_patched):
            await asyncio.wait_for(worker_loop(self.queue, self.store, "c1", stop, block_ms=0), 2)
        self.assertIn(("owner/repo", 7), self.store.issues)

    async def test_worker_loop_survives_errors(self):
        stop = asyncio.Event()
        calls = []

        async def broken_run_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                stop.set()
            raise ConnectionError("redis down")

        with patch("sift.worker.run_once", broken_run_once), patch("sift.worker.asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(worker_loop(self.queue, self.store, "c1", stop), 2)
        self.assertEqual(len(calls), 2)

    def test_backoff_is_capped(self):
        self.assertEqual(backoff(0), 5.0)
        self.assertEqual(backoff(1), 10.0)
        self.assertEqual(backoff(20), 300.0)


if __name__ == "__main__":
    unittest.main()
