"""Queue consumer: python -m sift.worker"""
import asyncio
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable

from sift.jobs import Job
from sift.queue.base import JobQueue
from sift.store.base import IssueStore

log = logging.getLogger("sift.worker")

MAX_ATTEMPTS = 5
Handler = Callable[[Job, IssueStore], Awaitable[None]]


def backoff(attempts: int) -> float:
    """5s, 10s, 20s, 40s, ... capped at 5 minutes."""
    return min(300.0, 5.0 * 2 ** attempts)


async def handle(job: Job, store: IssueStore) -> None:
    if not await store.apply(job):
        log.info("skip already-applied delivery %s", job.delivery_id)
        return
    # Phase 3+: triage newly opened issues and PRs here.


async def run_once(
    queue: JobQueue,
    store: IssueStore,
    consumer: str,
    handler: Handler = handle,
    block_ms: int = 5000,
) -> int:
    messages = await queue.read(consumer, block_ms=block_ms)
    for msg_id, job in messages:
        try:
            await handler(job, store)
        except Exception as e:
            if job.attempts + 1 >= MAX_ATTEMPTS:
                log.exception("dead-lettering %s after %d attempts", job.delivery_id, job.attempts + 1)
                await queue.dead_letter(msg_id, job, repr(e))
            else:
                delay = backoff(job.attempts)
                log.warning("retrying %s in %.0fs: %r", job.delivery_id, delay, e)
                await queue.retry(msg_id, job, delay)
        else:
            await queue.ack(msg_id)
    return len(messages)


async def main() -> None:
    import asyncpg
    from redis.asyncio import Redis

    from sift.config import Settings
    from sift.queue.redis_streams import RedisStreamQueue
    from sift.store.postgres import PostgresStore

    settings = Settings.from_env()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    queue = RedisStreamQueue(redis)
    await queue.setup()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    store = PostgresStore(pool)

    consumer = f"{socket.gethostname()}-{os.getpid()}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    log.info("worker %s started", consumer)
    try:
        while not stop.is_set():
            await run_once(queue, store, consumer)
    finally:
        await pool.close()
        await redis.aclose()
        log.info("worker %s stopped", consumer)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
