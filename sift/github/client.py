"""GitHub REST client authenticated as an App installation."""
import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

import httpx

from sift.github.ratelimit import retry_delay

API = "https://api.github.com"
REFRESH_MARGIN = 300  # refresh installation tokens 5 minutes before they expire


def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sift-triage",
        },
        timeout=10.0,
    )


def app_jwt(app_id: str, private_key: str, now: float | None = None) -> str:
    import jwt

    issued = int(now if now is not None else time.time())
    # iat is backdated 60s for clock drift; GitHub allows at most 10 minutes of validity.
    claims = {"iat": issued - 60, "exp": issued + 540, "iss": app_id}
    return jwt.encode(claims, private_key, algorithm="RS256")


class TokenSource(Protocol):
    async def get(self, installation_id: int) -> str: ...


class InstallationTokens:
    def __init__(
        self,
        app_id: str,
        private_key: str,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.time,
    ):
        self._app_id = app_id
        self._private_key = private_key
        self._http = http
        self._clock = clock
        self._cache: dict[int, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, installation_id: int) -> str:
        async with self._lock:
            cached = self._cache.get(installation_id)
            if cached and cached[1] - self._clock() > REFRESH_MARGIN:
                return cached[0]
            token = app_jwt(self._app_id, self._private_key, self._clock())
            r = await self._http.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json()
            expires_at = datetime.fromisoformat(data["expires_at"]).timestamp()
            self._cache[installation_id] = (data["token"], expires_at)
            return data["token"]


class GitHubClient:
    def __init__(
        self,
        tokens: TokenSource,
        http: httpx.AsyncClient,
        max_attempts: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self._tokens = tokens
        self._http = http
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock

    async def request(
        self, installation_id: int, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        for attempt in range(self._max_attempts):
            token = await self._tokens.get(installation_id)
            r = await self._http.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
            delay = retry_delay(r.status_code, r.headers, attempt, self._clock(), r.text)
            if delay is None or attempt == self._max_attempts - 1:
                r.raise_for_status()
                return r
            await self._sleep(delay)
        raise AssertionError("unreachable")

    async def list_labels(self, installation_id: int, repo: str) -> list[str]:
        names: list[str] = []
        page = 1
        while True:
            r = await self.request(
                installation_id, "GET", f"/repos/{repo}/labels",
                params={"per_page": 100, "page": page},
            )
            batch = r.json()
            names += [label["name"] for label in batch]
            if len(batch) < 100:
                return names
            page += 1

    async def add_labels(self, installation_id: int, repo: str, number: int, labels: list[str]) -> None:
        await self.request(
            installation_id, "POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": labels}
        )

    async def comment(self, installation_id: int, repo: str, number: int, body: str) -> None:
        await self.request(
            installation_id, "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        )
