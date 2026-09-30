"""Webhook receiver: uvicorn sift.app:app_factory --factory"""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from sift.config import Settings
from sift.github.events import parse_event
from sift.github.signature import verify_signature
from sift.queue.base import JobQueue


def create_app(settings: Settings, queue: JobQueue | None = None) -> FastAPI:
    if not settings.webhook_secret:
        raise RuntimeError("GITHUB_WEBHOOK_SECRET is not set")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if queue is not None:
            app.state.queue = queue
            yield
            return
        from redis.asyncio import Redis

        from sift.queue.redis_streams import RedisStreamQueue

        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.queue = RedisStreamQueue(redis)
        await app.state.queue.setup()
        try:
            yield
        finally:
            await redis.aclose()

    app = FastAPI(title="Sift", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/webhook", status_code=202)
    async def webhook(
        request: Request,
        x_github_event: str = Header(...),
        x_github_delivery: str = Header(...),
        x_hub_signature_256: str | None = Header(None),
    ) -> dict:
        body = await request.body()
        if not verify_signature(settings.webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")
        if x_github_event == "ping":
            return {"status": "pong"}
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON")

        job = parse_event(x_github_event, x_github_delivery, payload)
        if job is None:
            return {"status": "ignored"}
        queued = await request.app.state.queue.enqueue(job)
        return {"status": "queued" if queued else "duplicate"}

    return app


def app_factory() -> FastAPI:
    return create_app(Settings.from_env())
