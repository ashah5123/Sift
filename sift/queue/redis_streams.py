"""Job queue on Redis Streams.

- Deliveries are deduplicated with SET NX on the GitHub delivery id.
- Workers read through a consumer group; messages left pending by a crashed
  worker are reclaimed with XAUTOCLAIM.
- Retries wait in a sorted set scored by due time and are moved back onto the
  stream atomically by a Lua script.
- Jobs that keep failing go to a dead-letter stream.
"""
import time
from dataclasses import replace

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from sift.jobs import Job

_PROMOTE_DUE = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 100)
for _, member in ipairs(due) do
  redis.call('ZREM', KEYS[1], member)
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[2], '*', 'job', member)
end
return #due
"""

GROUP = "workers"
DEFAULT_BLOCK_MS = 5000


def make_redis(url: str) -> Redis:
    """Client settings for a queue that blocks on XREADGROUP.

    The read timeout must be well above the XREADGROUP block time, or every idle
    wait fails with TimeoutError (redis-py defaults both to 5s). The health check
    and keepalive recover connections that hosted Redis closes while idle.
    """
    return Redis.from_url(
        url,
        decode_responses=True,
        protocol=2,
        socket_timeout=DEFAULT_BLOCK_MS / 1000 + 25,
        socket_connect_timeout=10,
        socket_keepalive=True,
        health_check_interval=30,
    )


class RedisStreamQueue:
    def __init__(
        self,
        redis: Redis,
        prefix: str = "sift",
        dedupe_ttl_s: int = 7 * 24 * 3600,
        claim_idle_ms: int = 60_000,
        maxlen: int = 100_000,
    ):
        self.r = redis
        self.prefix = prefix
        self.stream = f"{prefix}:jobs"
        self.retry_key = f"{prefix}:retry"
        self.dead_stream = f"{prefix}:dead"
        self.dedupe_ttl_s = dedupe_ttl_s
        self.claim_idle_ms = claim_idle_ms
        self.maxlen = maxlen

    async def setup(self) -> None:
        try:
            await self.r.xgroup_create(self.stream, GROUP, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def _seen_key(self, delivery_id: str) -> str:
        return f"{self.prefix}:seen:{delivery_id}"

    async def enqueue(self, job: Job) -> bool:
        seen = self._seen_key(job.delivery_id)
        if not await self.r.set(seen, 1, nx=True, ex=self.dedupe_ttl_s):
            return False
        try:
            await self.r.xadd(self.stream, {"job": job.to_json()}, maxlen=self.maxlen, approximate=True)
        except Exception:
            # Let GitHub's redelivery go through if we never actually queued it.
            await self.r.delete(seen)
            raise
        return True

    async def read(
        self, consumer: str, count: int = 10, block_ms: int = DEFAULT_BLOCK_MS
    ) -> list[tuple[str, Job]]:
        await self.r.eval(_PROMOTE_DUE, 2, self.retry_key, self.stream, str(time.time()), str(self.maxlen))

        claimed = await self.r.xautoclaim(
            self.stream, GROUP, consumer, min_idle_time=self.claim_idle_ms, start_id="0-0", count=count
        )
        messages = claimed[1]
        if not messages:
            resp = await self.r.xreadgroup(
                GROUP, consumer, {self.stream: ">"}, count=count, block=block_ms or None
            )
            messages = resp[0][1] if resp else []
        return [(msg_id, Job.from_json(fields["job"])) for msg_id, fields in messages if fields]

    async def _finish(self, pipe, msg_id: str) -> None:
        pipe.xack(self.stream, GROUP, msg_id)
        pipe.xdel(self.stream, msg_id)
        await pipe.execute()

    async def ack(self, msg_id: str) -> None:
        async with self.r.pipeline(transaction=True) as pipe:
            await self._finish(pipe, msg_id)

    async def retry(self, msg_id: str, job: Job, delay_s: float) -> None:
        nxt = replace(job, attempts=job.attempts + 1)
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.zadd(self.retry_key, {nxt.to_json(): time.time() + delay_s})
            await self._finish(pipe, msg_id)

    async def dead_letter(self, msg_id: str, job: Job, error: str) -> None:
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.xadd(self.dead_stream, {"job": job.to_json(), "error": error[:2000]})
            await self._finish(pipe, msg_id)
