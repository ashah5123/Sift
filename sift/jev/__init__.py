import os

from sift.jev.base import FLAGS, Flag, JevClient
from sift.jev.mock import MockJev

__all__ = ["FLAGS", "Flag", "JevClient", "MockJev", "get_jev"]


def get_jev(backend: str | None = None) -> JevClient:
    backend = backend or os.environ.get("JEV_BACKEND", "mock")
    if backend == "mock":
        return MockJev()
    if backend == "local":
        from sift.jev.local import LocalJev

        return LocalJev()
    # TODO: TypeSafe Jev client once API access is set up (reads JEV_API_KEY).
    raise NotImplementedError(f"JEV_BACKEND={backend!r} is not implemented yet")
