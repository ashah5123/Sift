-- Idempotent: safe to run on every deploy.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repos (
    full_name         text PRIMARY KEY,
    installation_id   bigint,
    act_threshold     real NOT NULL DEFAULT 0.90,
    suggest_threshold real NOT NULL DEFAULT 0.60,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS issues (
    repo         text NOT NULL REFERENCES repos (full_name) ON DELETE CASCADE,
    number       integer NOT NULL,
    is_pr        boolean NOT NULL DEFAULT false,
    title        text NOT NULL,
    body         text NOT NULL DEFAULT '',
    labels       text[] NOT NULL DEFAULT '{}',
    state        text NOT NULL,
    state_reason text,
    author       text,
    created_at   timestamptz NOT NULL,
    updated_at   timestamptz NOT NULL,
    embedding    vector(384),  -- bge-small-en-v1.5, filled in Phase 3
    PRIMARY KEY (repo, number)
);

CREATE INDEX IF NOT EXISTS issues_embedding_idx
    ON issues USING hnsw (embedding vector_cosine_ops);

-- One row per webhook delivery. The primary key makes processing idempotent,
-- and label events by humans become maintainer corrections in Phase 5.
CREATE TABLE IF NOT EXISTS events (
    delivery_id text PRIMARY KEY,
    repo        text NOT NULL,
    number      integer NOT NULL,
    event       text NOT NULL,
    action      text NOT NULL,
    label       text,
    actor       text,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_repo_number_idx ON events (repo, number);
