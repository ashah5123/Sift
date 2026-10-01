"""Hand-labeling of flagged duplicates that the ground truth calls wrong.

Ground truth only knows duplicates that maintainers marked, so many "false"
flags are real duplicates nobody marked, and measured precision is a lower
bound. A random sample of the wrong flags is labeled by hand; the share that
are real duplicates corrects the estimate:

  true precision ~= (marked correct + share * wrong flags) / flagged
"""
import json
import math
import random
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sift.backtest.dataset import Record
from sift.backtest.replay import ReplayResult
from sift.models import Issue

LABELS_DIR = Path("labels")
ANSWERS = {"y": "yes", "n": "no", "u": "unsure"}

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLANK_LINES = re.compile(r"\n\s*\n+")


def labels_path(repo: str, detector: str) -> Path:
    return LABELS_DIR / f"{repo.replace('/', '__')}__{detector}.json"


def issue_url(repo: str, number: int) -> str:
    return f"https://github.com/{repo}/issues/{number}"


def build_sample(
    repo: str, records: list[Record], result: ReplayResult, threshold: float, n: int, seed: int = 0
) -> dict[str, Any]:
    """A seeded random sample of flags at `threshold` that disagree with ground truth."""
    flagged = [o for o in result.dups if o.best is not None and o.confidence >= threshold]
    wrong = sorted((o for o in flagged if o.best != o.target), key=lambda o: o.number)
    picked = sorted(random.Random(seed).sample(wrong, min(n, len(wrong))), key=lambda o: o.number)
    titles = {r.issue.number: r.issue.title for r in records}
    return {
        "repo": repo,
        "detector": result.detector,
        "threshold": threshold,
        "seed": seed,
        "labeled_by": None,  # who gave the verdicts: a person, or a model if one was used
        "flagged": len(flagged),
        "marked_correct": len(flagged) - len(wrong),
        "wrong_flags": len(wrong),
        "sample": [
            {
                "issue": o.number,
                "candidate": o.best,
                "confidence": round(o.confidence, 4),
                "marked_original": o.target,  # set when maintainers named a different original
                "issue_title": titles[o.number],
                "candidate_title": titles[o.best],
                "verdict": None,  # "yes" | "no" | "unsure"
                "note": "",
            }
            for o in picked
        ],
    }


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def save(labels: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def estimate(labels: dict[str, Any]) -> dict[str, Any]:
    verdicts = [row["verdict"] for row in labels["sample"]]
    yes, no = verdicts.count("yes"), verdicts.count("no")
    n = yes + no  # "unsure" is left out of the estimate, and reported
    flagged, correct, wrong = labels["flagged"], labels["marked_correct"], labels["wrong_flags"]

    def precision(share: float) -> float | None:
        return (correct + share * wrong) / flagged if flagged else None

    lo, hi = wilson(yes, n)
    return {
        "labeled": n,
        "yes": yes,
        "no": no,
        "unsure": verdicts.count("unsure"),
        "unlabeled": verdicts.count(None),
        "real_duplicate_share": yes / n if n else None,
        "measured_precision": precision(0.0),
        "estimated_precision": precision(yes / n) if n else None,
        "estimated_precision_95ci": [precision(lo), precision(hi)] if n else None,
    }


def excerpt(issue: Issue, limit: int = 800) -> str:
    body = _BLANK_LINES.sub("\n", _HTML_COMMENT.sub("", issue.body)).strip()
    return body if len(body) <= limit else body[:limit] + " [...]"


def label_interactively(
    labels: dict[str, Any],
    issues: dict[int, Issue],
    save_fn: Callable[[dict[str, Any]], None],
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Ask for a verdict on each unlabeled pair, saving after every answer. Returns answers given."""
    repo = labels["repo"]
    pending = [row for row in labels["sample"] if row["verdict"] is None]
    answered = 0
    for i, row in enumerate(pending, 1):
        new, old = issues[row["issue"]], issues[row["candidate"]]
        out(f"\n=== {i}/{len(pending)}  confidence {row['confidence']:.2f}")
        for tag, issue in (("NEW", new), ("EARLIER", old)):
            out(f"\n[{tag}] #{issue.number} {issue.title}\n{issue_url(repo, issue.number)}")
            out(excerpt(issue))
        if row["marked_original"] is not None:
            out(f"\n(maintainers marked #{row['issue']} a duplicate of #{row['marked_original']})")
        while True:
            answer = ask("\nSame bug? [y]es [n]o [u]nsure [s]kip [q]uit: ").strip().lower()[:1]
            if answer in ANSWERS or answer in ("s", "q"):
                break
        if answer == "q":
            break
        if answer == "s":
            continue
        row["verdict"] = ANSWERS[answer]
        save_fn(labels)
        answered += 1
    return answered
