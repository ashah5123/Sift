from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEMA = Path(__file__).with_name("schema.sql")

# libpq options asyncpg doesn't understand; it would forward them to the server
# as runtime settings and the connection would fail.
_UNSUPPORTED_PARAMS = {"channel_binding"}


def clean_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in _UNSUPPORTED_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(query)))


async def create_pool(dsn: str, min_size: int = 1, max_size: int = 5):
    import asyncpg

    return await asyncpg.create_pool(clean_dsn(dsn), min_size=min_size, max_size=max_size)


async def init_db(dsn: str) -> None:
    import asyncpg

    con = await asyncpg.connect(clean_dsn(dsn))
    try:
        await con.execute(SCHEMA.read_text())
    finally:
        await con.close()
