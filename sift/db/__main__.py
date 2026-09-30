"""Apply the schema: python -m sift.db"""
import asyncio

from sift.config import Settings
from sift.db import init_db

if __name__ == "__main__":
    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")
    asyncio.run(init_db(settings.database_url))
    print("schema applied")
