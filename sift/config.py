import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    webhook_secret: str = ""
    app_id: str = ""
    private_key: str = ""
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = ""
    jev_backend: str = "mock"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        key = env.get("GITHUB_PRIVATE_KEY", "").replace("\\n", "\n")
        key_path = env.get("GITHUB_PRIVATE_KEY_PATH")
        if not key and key_path:
            key = Path(key_path).expanduser().read_text()
        return cls(
            webhook_secret=env.get("GITHUB_WEBHOOK_SECRET", ""),
            app_id=env.get("GITHUB_APP_ID", ""),
            private_key=key,
            redis_url=env.get("REDIS_URL", cls.redis_url),
            database_url=env.get("DATABASE_URL", ""),
            jev_backend=env.get("JEV_BACKEND", "mock"),
        )
