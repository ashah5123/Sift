# Sift

A free GitHub App that triages new issues and PRs for open-source maintainers: finds duplicates, labels and routes, scores priority against your rubric, and flags missing repro steps, low-effort AI-generated reports and publicly posted security issues. It acts only when confident.

Status: early development. See [PLAN.md](PLAN.md) for the roadmap.

## Backtest results

Replayed in time order over real history from React (5,000 issues) and VS Code (4,000 issues), with maintainers' own duplicate markings as ground truth. Local baselines only so far; Jev comes in Phase 3.

| Repo | Detector | Original in top-10 | False-action rate @0.5 | p50 latency |
|---|---|---|---|---|
| facebook/react | word overlap (Jaccard) | 27.9% | 24.2% | 1.5 ms |
| facebook/react | TF-IDF + template stripping | **67.4%** | **6.4%** | 0.8 ms |
| microsoft/vscode | word overlap (Jaccard) | 24.0% | 21.2% | 1.5 ms |
| microsoft/vscode | TF-IDF + template stripping | **38.0%** | **3.6%** | 1.2 ms |

Similarity search finds the right original often, but the single most similar issue is rarely it. That is why Sift retrieves candidates locally and lets a model judge them. Method, caveats and full numbers: [docs/backtest.md](docs/backtest.md).

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

- Run against GitHub: [docs/github-app-setup.md](docs/github-app-setup.md)
- Backtests: [docs/backtest.md](docs/backtest.md)
