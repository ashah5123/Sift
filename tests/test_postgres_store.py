"""Integration tests against Postgres + pgvector. Set SIFT_TEST_DATABASE_URL to run (CI does)."""
import unittest
import uuid

from tests.helpers import DATABASE_URL, has, make_job

RUN = bool(DATABASE_URL) and has("asyncpg")


@unittest.skipUnless(RUN, "SIFT_TEST_DATABASE_URL not set")
class TestPostgresStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg

        from sift.db import init_db
        from sift.store.postgres import PostgresStore

        await init_db(DATABASE_URL)
        await init_db(DATABASE_URL)  # idempotent
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        self.store = PostgresStore(self.pool)
        self.repo = f"owner/{uuid.uuid4().hex[:8]}"

    async def asyncTearDown(self):
        await self.pool.execute("DELETE FROM events WHERE repo = $1", self.repo)
        await self.pool.execute("DELETE FROM repos WHERE full_name = $1", self.repo)
        await self.pool.close()

    def job(self, delivery, **kw):
        return make_job(f"{self.repo}-{delivery}", repo=self.repo, **kw)

    async def test_apply_is_idempotent(self):
        self.assertTrue(await self.store.apply(self.job("d1")))
        self.assertFalse(await self.store.apply(self.job("d1")))
        count = await self.pool.fetchval("SELECT count(*) FROM events WHERE repo = $1", self.repo)
        self.assertEqual(count, 1)
        row = await self.pool.fetchrow("SELECT * FROM issues WHERE repo = $1", self.repo)
        self.assertEqual(row["title"], "Crash on startup")
        self.assertEqual(row["labels"], ["bug"])
        inst = await self.pool.fetchval(
            "SELECT installation_id FROM repos WHERE full_name = $1", self.repo
        )
        self.assertEqual(inst, 42)

    async def test_older_event_does_not_overwrite_newer(self):
        await self.store.apply(self.job("d2", updated_at="2026-01-02T00:00:00Z", item={"title": "new"}))
        await self.store.apply(self.job("d1", updated_at="2026-01-01T00:00:00Z", item={"title": "old"}))
        title = await self.pool.fetchval("SELECT title FROM issues WHERE repo = $1", self.repo)
        self.assertEqual(title, "new")


if __name__ == "__main__":
    unittest.main()
