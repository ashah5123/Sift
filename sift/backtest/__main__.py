"""Backtest CLI.

  python -m sift.backtest fetch     --repo facebook/react --limit 5000
  python -m sift.backtest stats     --repo facebook/react
  python -m sift.backtest run       --repo facebook/react --detector jaccard
  python -m sift.backtest compare
  python -m sift.backtest sample    --repo facebook/react --detector tfidf
  python -m sift.backtest label     --repo facebook/react --detector tfidf
  python -m sift.backtest precision --repo facebook/react --detector tfidf
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from sift.backtest import dataset, labeling
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


def cmd_sample(args: argparse.Namespace) -> None:
    path = labeling.labels_path(args.repo, args.detector)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} exists; it may hold verdicts. Pass --force to replace it.")
    records = _load(args.repo)
    result = asyncio.run(replay(records, DETECTORS[args.detector](), k=args.k))
    labels = labeling.build_sample(args.repo, records, result, args.threshold, args.n, args.seed)
    labeling.save(labels, path)
    log(f"{labels['wrong_flags']} of {labels['flagged']} flags at >= {args.threshold} disagree with "
        f"ground truth; sampled {len(labels['sample'])} to {path}")
    log(f"Next: python -m sift.backtest label --repo {args.repo} --detector {args.detector}")


def _load_labels(args: argparse.Namespace) -> tuple[Path, dict]:
    path = labeling.labels_path(args.repo, args.detector)
    if not path.exists():
        raise SystemExit(f"No sample yet. Run: python -m sift.backtest sample --repo {args.repo} "
                         f"--detector {args.detector}")
    return path, labeling.load(path)


def cmd_label(args: argparse.Namespace) -> None:
    path, labels = _load_labels(args)
    issues = {r.issue.number: r.issue for r in _load(args.repo)}
    try:
        n = labeling.label_interactively(labels, issues, lambda l: labeling.save(l, path))
    except (KeyboardInterrupt, EOFError):
        n = None
    log(f"\nSaved to {path}" + (f" ({n} new verdicts)" if n is not None else ""))
    cmd_precision(args)


def cmd_precision(args: argparse.Namespace) -> None:
    _, labels = _load_labels(args)
    e = labeling.estimate(labels)
    pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"
    print(f"{args.repo}  detector={labels['detector']}  threshold={labels['threshold']}")
    print(f"  labeled by {labels.get('labeled_by') or 'unknown'}")
    print(f"  flagged {labels['flagged']}: {labels['marked_correct']} match ground truth, "
          f"{labels['wrong_flags']} don't")
    print(f"  sample of {len(labels['sample'])}: {e['yes']} real duplicates, {e['no']} not, "
          f"{e['unsure']} unsure, {e['unlabeled']} unlabeled")
    print(f"  measured precision (ground truth only)  {pct(e['measured_precision'])}")
    if e["estimated_precision"] is None:
        print("  estimated true precision                - (label some pairs first)")
    else:
        lo, hi = e["estimated_precision_95ci"]
        print(f"  estimated true precision                {pct(e['estimated_precision'])} "
              f"(95% CI {pct(lo)} - {pct(hi)}, n={e['labeled']})")


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

    sample = sub.add_parser("sample", help="sample flags that ground truth calls wrong, for hand-labeling")
    sample.add_argument("--repo", required=True)
    sample.add_argument("--detector", choices=sorted(DETECTORS), default="tfidf")
    sample.add_argument("--threshold", type=float, default=0.5)
    sample.add_argument("--n", type=int, default=50, help="pairs to sample")
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument("--k", type=int, default=10)
    sample.add_argument("--force", action="store_true", help="replace an existing sample and its verdicts")
    sample.set_defaults(func=cmd_sample)

    label = sub.add_parser("label", help="hand-label sampled pairs in the terminal (resumable)")
    label.add_argument("--repo", required=True)
    label.add_argument("--detector", choices=sorted(DETECTORS), default="tfidf")
    label.set_defaults(func=cmd_label)

    precision = sub.add_parser("precision", help="true-precision estimate from hand labels")
    precision.add_argument("--repo", required=True)
    precision.add_argument("--detector", choices=sorted(DETECTORS), default="tfidf")
    precision.set_defaults(func=cmd_precision)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
