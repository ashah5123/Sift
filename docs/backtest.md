# Backtest harness

Replays real issue history from public repos and measures how often Sift would have been right, before it acts on anyone's repo.

## Commands

```bash
python -m sift.backtest fetch   --repo facebook/react --limit 5000   # download to data/ (gitignored)
python -m sift.backtest stats   --repo facebook/react                # what's in the dataset
python -m sift.backtest run     --repo facebook/react --detector tfidf
python -m sift.backtest compare                                      # markdown table of results/
```

Hand-labeling, to measure true precision (see below):

```bash
python -m sift.backtest sample    --repo facebook/react --detector tfidf   # sample to labels/ (committed)
python -m sift.backtest label     --repo facebook/react --detector tfidf   # verdicts in the terminal, resumable
python -m sift.backtest precision --repo facebook/react --detector tfidf   # corrected estimate + 95% CI
```

`fetch` uses `GITHUB_TOKEN` or your `gh` login. Detectors: `jaccard`, `tfidf`, `mock-jev`.

## Ground truth

Taken from what maintainers already did:

- **Duplicates**: the issue was closed as duplicate or carries a duplicate label, *and* a comment names the original (`Duplicate of #123`, GitHub's own marker). Issues closed as "not planned" are checked too, since maintainers often close duplicates that way.
- **Labels**: the issue's final labels, minus process labels (`stale`, `triage-needed`, `Status: Unconfirmed`, `verified`, ...).

Duplicates whose original can't be identified, or whose original is older than the downloaded window, are skipped and counted separately.

## No peeking at the future

- Issues are replayed oldest first. Each is scored against only the issues created before it, then added to the pool.
- Detectors never see labels or duplicate links, only title and body.
- Everything a detector learns (IDF weights, which lines are template text) comes from earlier issues.
- `tests/test_backtest.py::test_no_future_leakage` checks this.

Known limitation: issue text is the current version, so edits made after creation are visible.

## Metrics

| Metric | Meaning |
|---|---|
| Original in top-10 | Retrieval quality: for known duplicates, is the original among the 10 candidates? This caps what any judge (Jev) can achieve. |
| Precision @ t | Of issues flagged as duplicates at confidence ≥ t, how many point at the right original |
| Recall @ t | Of known duplicates, how many were flagged with the right original |
| False-action rate @ t | Of all scored issues, how many would get a wrong duplicate flag. The number that gets a bot uninstalled. |

## Results (baselines, Phase 2)

React: 5,000 most recent issues (Nov 2020 – Sep 2026), 43 scorable duplicates. VS Code: 4,000 most recent issues (Aug – Sep 2026), 50 scorable duplicates.

| Repo | Detector | Original in top-10 | Precision @0.5 | Recall @0.5 | False-action rate @0.5 | p50 latency |
|---|---|---|---|---|---|---|
| facebook/react | jaccard | 27.9% | 0.3% | 9.3% | 24.2% | 1.5 ms |
| facebook/react | tfidf | **67.4%** | 1.6% | 11.6% | 6.4% | 0.8 ms |
| microsoft/vscode | jaccard | 24.0% | 0.1% | 2.0% | 21.2% | 1.5 ms |
| microsoft/vscode | tfidf | **38.0%** | 0.8% | 2.0% | 3.6% | 1.2 ms |

`mock-jev` is a rule-based placeholder for Jev, so its accuracy numbers are not meaningful. Its cost column only checks the token accounting.

### What this shows

1. **Retrieval works; judging doesn't.** TF-IDF puts the true original in the top 10 for 67% of React duplicates. But the single most similar issue is rarely the original. Similar topic is not the same bug. That gap is exactly what the retrieve-then-decide design with Jev is for.
2. **Issue templates break naive similarity.** Unfilled bug-report templates look identical to each other. Jaccard flags 24% of React issues at 0.5. Stripping template lines learned from earlier issues and weighting rare words cut that to 6.4%.
3. **Precision here is a lower bound.** Only about 1% of issues are marked duplicates. Many flagged "false" matches are real, unmarked duplicates, e.g. React DevTools crash reports with identical error text. A hand-labeled sample is needed for a true precision number.
4. **Labels need per-repo vocabulary.** The keyword mock labels 80% correctly on React when it answers, but only answers 34% of the time. On VS Code, label names rarely appear in issue text. Predicting the most frequent label is a strong baseline (57% / 38%) that any model must beat.

### True precision from hand labels

Ground truth only knows duplicates maintainers marked, so precision above is a lower bound. To correct it, `sample` takes a seeded random sample of 50 flags at ≥ 0.5 that ground truth calls wrong. `label` shows each pair with links and asks whether they are the same bug. `precision` then estimates:

```
true precision ≈ (flags matching ground truth + share judged real × flags not matching) / all flags
```

with a 95% Wilson interval on the share. "Unsure" verdicts are left out and reported. Samples and verdicts live in `labels/` and are committed, so the number can be checked.

| Repo | Detector | Flags @0.5 | Match ground truth | Sampled | Real / not / unsure | Estimated true precision @0.5 |
|---|---|---|---|---|---|---|
| facebook/react | tfidf | 319 | 5 (1.6%) | 50 | 35 / 10 / 5 | **78%** (95% CI 64–88%) |
| microsoft/vscode | tfidf | 132 | 1 (0.8%) | 50 | 15 / 34 / 1 | **31%** (95% CI 20–45%) |

**Who labeled:** these verdicts were made by Claude (claude-opus-5-5), not by a person, and have not been human-reviewed yet. Each pair has a one-line reason in `labels/*.json`. Treat the numbers as provisional until a person confirms them. Re-running `label` after clearing verdicts, or editing the JSON, replaces them.

What the real duplicates are:

- **React (35):** 12 DevTools crash reports with the same error and stack (`Cannot remove node`, `Could not inspect element`, ...). 11 copies of the same bot-filed dependency-update issue. 10 reposts of the same report, usually by the same author. 2 other matching bug reports. Most are easy, near-exact text matches.
- **VS Code (15):** 11 reposts by the same reporter, plus 4 independent reports of the same bug (a CVE advisory, an MCP race, an installer error, an a11y bug). The 34 misses are mostly unrelated issues that share VS Code's long system-info template, plus empty template submissions.

So ground truth badly undercounts duplicates, but the easy duplicates (reposts, bot spam, identical crash signatures) inflate the estimate. The 0.5 threshold is still far too loose to act on, especially on VS Code. The judge in Phase 3 has to beat these numbers on the same samples.

### Bugs the backtest caught

- The first TF-IDF version froze each issue's weight when the issue was added, so early short issues matched almost everything (574 false 0.9+ matches on VS Code). Norms are now refreshed as the index grows. A regression test covers this.
