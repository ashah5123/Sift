"""Integration tests against a real Redis. Set SIFT_TEST_REDIS_URL to run (CI does)."""
import unittest
import uuid

from tests.helpers import REDIS_URL, has, make_job

RUN = bool(REDIS_URL) and has("redis")


@unittest.skipUnless(RUN, "SIFT_TEST_REDIS_URL not set")
class TestRedisStreamQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from redis.asyncio import Redis

        from sift.queue.redis_streams import RedisStreamQueue

        self.redis = Redis.from_url(REDIS_URL, decode_responses=True)
        self.prefix = f"test-{uuid.uuid4().hex}"
        self.make = lambda **kw: RedisStreamQueue(self.redis, prefix=self.prefix, **kw)
        self.q = self.make()
        await self.q.setup()
        await self.q.setup()  # idempotent

    async def asyncTearDown(self):
        keys = [k async for k in self.redis.scan_iter(f"{self.prefix}:*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.aclose()

    async def test_dedupe_read_ack(self):
        self.assertTrue(await self.q.enqueue(make_job("d1")))
        self.assertFalse(await self.q.enqueue(make_job("d1")))
        msgs = await self.q.read("c1", block_ms=100)
        self.assertEqual([job.delivery_id for _, job in msgs], ["d1"])
        await self.q.ack(msgs[0][0])
        self.assertEqual(await self.redis.xlen(self.q.stream), 0)
        self.assertEqual(await self.q.read("c1", block_ms=100), [])

    async def test_retry_is_delayed_then_redelivered(self):
        await self.q.enqueue(make_job("d1"))
        [(msg_id, job)] = await self.q.read("c1", block_ms=100)
        await self.q.retry(msg_id, job, delay_s=0)
        [(_, again)] = await self.q.read("c1", block_ms=100)
        self.assertEqual(again.attempts, 1)

        await self.q.retry(msg_id, again, delay_s=3600)
        self.assertEqual(await self.q.read("c1", block_ms=100), [])
        self.assertEqual(await self.redis.zcard(self.q.retry_key), 1)

    async def test_dead_letter(self):
        await self.q.enqueue(make_job("d1"))
        [(msg_id, job)] = await self.q.read("c1", block_ms=100)
        await self.q.dead_letter(msg_id, job, "RuntimeError('boom')")
        dead = await self.redis.xrange(self.q.dead_stream)
        self.assertEqual(len(dead), 1)
        self.assertIn("boom", dead[0][1]["error"])

    async def test_crashed_consumer_messages_are_reclaimed(self):
        q = self.make(claim_idle_ms=0)
        await q.enqueue(make_job("d1"))
        await q.read("crashed", block_ms=100)  # never acked
        [(_, job)] = await q.read("healthy", block_ms=100)
        self.assertEqual(job.delivery_id, "d1")


if __name__ == "__main__":
    unittest.main()
