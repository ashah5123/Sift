import asyncpg

from sift.jobs import Job, parse_ts

_INSERT_EVENT = """
INSERT INTO events (delivery_id, repo, number, event, action, label, actor)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (delivery_id) DO NOTHING
RETURNING 1
"""

_UPSERT_REPO = """
INSERT INTO repos (full_name, installation_id) VALUES ($1, $2)
ON CONFLICT (full_name) DO UPDATE
SET installation_id = COALESCE(EXCLUDED.installation_id, repos.installation_id)
"""

_UPSERT_ISSUE = """
INSERT INTO issues (repo, number, is_pr, title, body, labels, state, state_reason, author,
                    created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (repo, number) DO UPDATE SET
    title = EXCLUDED.title,
    body = EXCLUDED.body,
    labels = EXCLUDED.labels,
    state = EXCLUDED.state,
    state_reason = EXCLUDED.state_reason,
    updated_at = EXCLUDED.updated_at
WHERE issues.updated_at <= EXCLUDED.updated_at
"""


class PostgresStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def apply(self, job: Job) -> bool:
        item = job.item
        created = parse_ts(item["created_at"])
        updated = parse_ts(item.get("updated_at") or item["created_at"])
        async with self.pool.acquire() as con, con.transaction():
            inserted = await con.fetchval(
                _INSERT_EVENT, job.delivery_id, job.repo, job.number,
                job.event, job.action, job.label, job.actor,
            )
            if not inserted:
                return False
            await con.execute(_UPSERT_REPO, job.repo, job.installation_id)
            await con.execute(
                _UPSERT_ISSUE, job.repo, job.number, job.event == "pull_request",
                item.get("title") or "", item.get("body") or "", item.get("labels") or [],
                item.get("state") or "open", item.get("state_reason"), item.get("author"),
                created, updated,
            )
        return True
