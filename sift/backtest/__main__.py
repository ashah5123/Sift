"""Backtest CLI.

  python -m sift.backtest fetch --repo fastapi/fastapi --limit 5000
  python -m sift.backtest stats --repo fastapi/fastapi
  python -m sift.backtest run   --repo fastapi/fastapi --detector jaccard
  python -m sift.backtest compare
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from sift.backtest import dataset
from sift.backtest.detectors import DETECTORS
from sift.backtest.loader import GitHubREST, fetch_repo, github_token
from sift.backtest.replay import replay, summary
from sift.backtest.report import format_report
from sift.jev.mock import MockJev

RESULTS_DIR = Path("results")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_fetch(args: argparse.Namespace) -> None:
    log(f"Fetching {args.repo} (up to {args.limit} issues)")
    rows = fetch_repo(args.repo, GitHubREST(github_token()), limit=args.limit, log=log)
    path = dataset.dataset_path(args.repo)
    dataset.save(rows, path)
    dups = sum(r["is_duplicate"] for r in rows)
    linked = sum(r["duplicate_of"] is not None for r in rows)
    log(f"Saved {len(rows)} issues to {path} ({dups} duplicates, {linked} with a known original)")


def _load(repo: str) -> list[dataset.Record]:
    path = dataset.dataset_path(repo)
    if not path.exists():
        raise SystemExit(f"No data for {repo}. Run: python -m sift.backtest fetch --repo {repo}")
    return dataset.load(path)


def cmd_stats(args: argparse.Namespace) -> None:
    records = _load(args.repo)
    numbers = {r.issue.number for r in records}
    dups = [r for r in records if r.is_duplicate]
    linked = [r for r in dups if r.duplicate_of is not None]
    in_data = [r for r in linked if r.duplicate_of in numbers]
    labels = Counter(label for r in records for label in r.topic_labels)
    print(f"{args.repo}: {len(records)} issues, "
          f"{records[0].issue.created_at:%Y-%m-%d} to {records[-1].issue.created_at:%Y-%m-%d}")
    print(f"  duplicates: {len(dups)}  with known original: {len(linked)}  "
          f"original also in dataset: {len(in_data)}")
    print(f"  issues with topic labels: {sum(bool(r.topic_labels) for r in records)}")
    print("  top labels: " + ", ".join(f"{name} ({n})" for name, n in labels.most_common(10)))


def cmd_run(args: argparse.Namespace) -> None:
    records = _load(args.repo)
    detector = DETECTORS[args.detector]()
    result = asyncio.run(
        replay(records, detector, k=args.k, eval_last=args.eval_last, label_jev=MockJev())
    )
    s = summary(result)
    print(format_report(args.repo, s))
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{args.repo.replace('/', '__')}__{args.detector}.json"
    out.write_text(json.dumps({"repo": args.repo, **s}, indent=2) + "\n")
    log(f"\nSaved {out}")


def cmd_compare(args: argparse.Namespace) -> None:
    """Markdown table of every saved result, at one confidence threshold."""
    rows = [json.loads(p.read_text()) for p in sorted(RESULTS_DIR.glob("*.json"))]
    if not rows:
        raise SystemExit("No results yet. Run: python -m sift.backtest run --repo ...")
    pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"
    print(f"| Repo | Detector | Original in top-10 | Precision @{args.threshold} | "
          f"Recall @{args.threshold} | False-action rate @{args.threshold} | p50 latency | Cost / 1k issues |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        d = r["duplicates"]
        at = next(s for s in d["sweep"] if abs(s["threshold"] - args.threshold) < 1e-9)
        recall_k = next(v for key, v in d.items() if key.startswith("recall_at_"))
        print(f"| {r['repo']} | {r['detector']} | {pct(recall_k)} | {pct(at['precision'])} | "
              f"{pct(at['recall'])} | {pct(at['false_action_rate'])} | "
              f"{r['latency_ms']['p50']:.1f} ms | ${r['est_cost_per_1k_issues_usd']:.3f} |")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m sift.backtest")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download issue history from GitHub")
    fetch.add_argument("--repo", required=True)
    fetch.add_argument("--limit", type=int, default=5000, help="most recent N issues")
    fetch.set_defaults(func=cmd_fetch)

    stats = sub.add_parser("stats", help="summarize a downloaded dataset")
    stats.add_argument("--repo", required=True)
    stats.set_defaults(func=cmd_stats)

    run = sub.add_parser("run", help="replay history and print metrics")
    run.add_argument("--repo", required=True)
    run.add_argument("--detector", choices=sorted(DETECTORS), default="jaccard")
    run.add_argument("--k", type=int, default=10, help="candidates retrieved per issue")
    run.add_argument("--eval-last", type=int, help="only score the last N issues (earlier ones still form the pool)")
    run.set_defaults(func=cmd_run)

    compare = sub.add_parser("compare", help="markdown table of all saved results")
    compare.add_argument("--threshold", type=float, default=0.5)
    compare.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
