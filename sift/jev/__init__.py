import os

from sift.jev.base import FLAGS, Flag, JevClient
from sift.jev.mock import MockJev

__all__ = ["FLAGS", "Flag", "JevClient", "MockJev", "get_jev"]


def get_jev(backend: str | None = None) -> JevClient:
    backend = backend or os.environ.get("JEV_BACKEND", "mock")
    if backend == "mock":
        return MockJev()
    # TODO: real client once Jev access is granted (reads JEV_API_KEY).
    raise NotImplementedError(f"JEV_BACKEND={backend!r} is not implemented yet")
