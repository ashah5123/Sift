# Sift implementation plan

Triage bot for open-source maintainers. Each new issue/PR is deduplicated, labeled, prioritized and checked for red flags by Jev, and the bot acts only when confident.

## Principles
- **Measure first.** The backtest harness is built before the intelligence, so every change has a number.
- **Jev behind an interface** (`sift/jev/base.py`). `MockJev` stands in until API access; results are comparable across backends.
- **Never wrong in public.** Default to silence. Act only above a per-repo calibrated threshold.
- **No leakage.** Backtests replay in time order; a new issue only sees issues that existed at its `created_at`.
- **Idempotent workers.** Every webhook delivery id is processed at most once in effect.

## Architecture
```
GitHub webhook -> FastAPI (verify HMAC) -> Redis Stream -> worker(s)
worker: load issue -> embed -> pgvector top-10 -> Jev (parallel) -> policy -> GitHub API
Postgres: issues, embeddings, decisions, corrections, repo_config
Dashboard (React) reads decisions + corrections
```

## Phases

### Phase 0 - Repo and skeleton (day 1)
- [x] Public GitHub repo, Python project, `.env.example`
- [x] Core types, `JevClient` protocol, `MockJev`, policy (act/suggest/silent)
- [x] CI: compile check + tests on push
- **Done when:** `python -m unittest` passes on a clean clone.

### Phase 1 - GitHub App + ingestion (week 1)
- [x] Register GitHub App (issues/PR read+write, webhooks); install on own repos — see `docs/github-app-setup.md`
- [x] `POST /webhook`: verify `X-Hub-Signature-256`, dedupe on `X-GitHub-Delivery`, enqueue to Redis Stream
- [x] Worker with consumer group, retries with backoff, dead-letter stream
- [x] Installation-token auth (JWT -> token), rate-limit handling (`Retry-After`, secondary limits)
- [x] Postgres schema (`sift/db/schema.sql`), store issues
- [x] Deploy webhook + worker to a free host (Render, worker in-process) and verify end to end
- **Done when:** opening an issue on a test repo stores a row exactly once, even if the delivery is replayed.

### Phase 2 - Backtest harness (week 1-2)
- [x] Loader: pull issues from big repos (React, VS Code) to JSONL, with ground truth (duplicate label / "Duplicate of #N", human labels). FastAPI dropped: it moved issues to Discussions and has almost no marked duplicates.
- [x] Time-ordered replay with an as-of pool
- [x] Metrics: duplicate precision/recall@k, label accuracy, false-action rate, latency, cost per 1k issues
- [x] Baselines: Jaccard, TF-IDF with template stripping (embeddings baseline moves to Phase 3)
- [x] Labeling tooling: seeded sample of flagged "false" duplicates, terminal labeler, corrected precision with 95% CI (`sample` / `label` / `precision`)
- [x] Label the samples in `labels/` (50 pairs each, React + VS Code) and record true precision in `docs/backtest.md`: TF-IDF @0.5 is ~78% on React, ~31% on VS Code. Labeled by Claude, not yet human-reviewed.
- **Done when:** `python -m sift.backtest run --repo facebook/react --detector jaccard` prints a metrics table, and true precision is reported from labeled samples.

### Phase 3 - Duplicate detection (week 2-3)
- [x] BGE embeddings (local); measure recall@10 of retrieval stage alone: 67% React (ties TF-IDF), 86% VS Code (vs 38%). Hybrid fusion didn't help.
- [ ] Embed issues in the worker and search with pgvector (`issues.embedding`)
- [ ] Jev pairwise judgment over top-10 in parallel; measure end-to-end precision
- [ ] Post "looks like #N" comment

### Phase 4 - Labels, priority, red flags (week 3-4)
- [ ] Labels chosen from the repo's real label list; owner from CODEOWNERS
- [ ] Priority against `.github/sift.yml` rubric
- [ ] Flags: AI slop, missing repro, public security issue
- [ ] Confidence gating: act / suggest / silent

### Phase 5 - Feedback and calibration (week 4-5)
- [ ] Record corrections (label removed, duplicate reopened) via webhooks
- [ ] Reliability diagram + ECE per task; per-repo threshold tuning
- [ ] React dashboard: accuracy over time, action mix, corrections

### Phase 6 - Launch (week 5-6)
- [ ] Head-to-head table: Jev vs local LLM vs embeddings-only (accuracy, latency, $/1k issues) at top of README
- [ ] Marketplace listing, privacy policy, onboarding docs
- [ ] Install on 10+ real repos; publish real usage numbers

## Risks
| Risk | Mitigation |
|---|---|
| Jev access delayed | MockJev + local LLM backend behind same interface |
| Ground truth is noisy | Report on human-confirmed duplicates only; state the sampling |
| Bot wrong in public | Silent by default; high act threshold; one-click undo; shadow mode for new repos |
| Private repo data | Store only what's needed; document retention; no training on customer data |
| Free-tier limits | Neon/Supabase free Postgres, small embedding dim (bge-small, 384) |
