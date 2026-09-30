# Sift

A free GitHub App that triages new issues and PRs for open-source maintainers: finds duplicates, labels and routes, scores priority against your rubric, and flags missing repro steps, low-effort AI-generated reports and publicly posted security issues. It acts only when confident.

Status: early development. See [PLAN.md](PLAN.md) for the roadmap.

Backtest results and the Jev vs. local LLM vs. embeddings-only comparison will go here.

## Architecture

```
GitHub webhook -> FastAPI (/webhook, HMAC verified)
               -> Redis Stream (deduped by delivery id)
               -> worker (consumer group, retries with backoff, dead-letter stream)
               -> Postgres + pgvector (events + issues, idempotent upserts)
```

## Development

```bash
pip install -e .
python -m unittest discover
```

Redis and Postgres integration tests run when `SIFT_TEST_REDIS_URL` and `SIFT_TEST_DATABASE_URL` are set; CI provides both.

To run against GitHub, see [docs/github-app-setup.md](docs/github-app-setup.md).
