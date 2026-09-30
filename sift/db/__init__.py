from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


async def init_db(dsn: str) -> None:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        await con.execute(SCHEMA.read_text())
    finally:
        await con.close()
