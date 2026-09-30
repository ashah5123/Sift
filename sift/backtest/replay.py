"""Time-ordered replay.

Issues are fed oldest first. Each one is evaluated against only the issues
created before it, then added to the pool. Detectors never see labels or
duplicate links, only title and body.
"""
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field, replace

from sift.backtest.dataset import Record
from sift.backtest.detectors import DuplicateDetector
from sift.jev.base import JevClient

THRESHOLDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
JEV_PRICE_PER_M_TOKENS = 0.04


@dataclass(frozen=True)
class DupOutcome:
    number: int
    target: int | None      # ground-truth original, None if not a duplicate
    best: int | None        # detector's top pick
    confidence: float
    target_in_candidates: bool


@dataclass(frozen=True)
class LabelOutcome:
    truth: tuple[str, ...]
    predicted: str          # "none" = abstained
    baseline: str | None    # most frequent label seen so far


@dataclass
class ReplayResult:
    detector: str
    k: int
    evaluated: int = 0
    unverifiable: int = 0   # duplicates whose original is unknown or outside the dataset
    dups: list[DupOutcome] = field(default_factory=list)
    labels: list[LabelOutcome] = field(default_factory=list)
    latencies_s: list[float] = field(default_factory=list)
    tokens_used: int = 0


def _visible(record: Record):
    """What a detector is allowed to see: no labels, no duplicate link."""
    return replace(record.issue, labels=(), duplicate_of=None)


async def replay(
    records: list[Record],
    detector: DuplicateDetector,
    k: int = 10,
    eval_last: int | None = None,
    label_jev: JevClient | None = None,
) -> ReplayResult:
    result = ReplayResult(detector=detector.name, k=k)
    start = max(0, len(records) - eval_last) if eval_last else 0
    seen: set[int] = set()
    label_counts: Counter[str] = Counter()

    for i, record in enumerate(records):
        issue = _visible(record)
        if i >= start:
            result.evaluated += 1
            if record.is_duplicate and record.duplicate_of not in seen:
                result.unverifiable += 1
            else:
                t0 = time.perf_counter()
                candidates = await detector.candidates(issue, k)
                best, confidence = await detector.decide(issue, candidates)
                result.latencies_s.append(time.perf_counter() - t0)
                target = record.duplicate_of if record.is_duplicate else None
                result.dups.append(
                    DupOutcome(
                        number=record.issue.number,
                        target=target,
                        best=best.number if best else None,
                        confidence=confidence,
                        target_in_candidates=target is not None
                        and any(c.number == target for c, _ in candidates),
                    )
                )

            truth = record.topic_labels
            if label_jev is not None and truth:
                known = sorted(label_counts)
                predicted = (await label_jev.pick_label(issue, known)).answer if known else "none"
                baseline = label_counts.most_common(1)[0][0] if label_counts else None
                result.labels.append(LabelOutcome(truth, predicted, baseline))

        detector.add(issue)
        seen.add(record.issue.number)
        label_counts.update(record.topic_labels)

    result.tokens_used = detector.tokens_used
    return result


def duplicate_metrics(result: ReplayResult) -> dict:
    outcomes = result.dups
    n = len(outcomes)
    positives = [o for o in outcomes if o.target is not None]
    sweep = []
    for t in THRESHOLDS:
        predicted = [o for o in outcomes if o.best is not None and o.confidence >= t]
        correct = sum(o.best == o.target for o in predicted)
        sweep.append({
            "threshold": t,
            "flagged": len(predicted),
            "precision": correct / len(predicted) if predicted else None,
            "recall": correct / len(positives) if positives else None,
            "false_action_rate": (len(predicted) - correct) / n if n else None,
        })
    return {
        "scored_issues": n,
        "known_duplicates": len(positives),
        f"recall_at_{result.k}": (
            sum(o.target_in_candidates for o in positives) / len(positives) if positives else None
        ),
        "sweep": sweep,
    }


def label_metrics(result: ReplayResult) -> dict | None:
    outcomes = result.labels
    if not outcomes:
        return None
    answered = [o for o in outcomes if o.predicted != "none"]
    correct = sum(o.predicted in o.truth for o in answered)
    return {
        "labeled_issues": len(outcomes),
        "coverage": len(answered) / len(outcomes),
        "accuracy_when_answered": correct / len(answered) if answered else None,
        "accuracy_overall": correct / len(outcomes),
        "most_frequent_baseline": sum(o.baseline in o.truth for o in outcomes) / len(outcomes),
    }


def summary(result: ReplayResult) -> dict:
    lat = sorted(result.latencies_s)
    per_issue_tokens = result.tokens_used / len(lat) if lat else 0
    return {
        "detector": result.detector,
        "evaluated": result.evaluated,
        "unverifiable_duplicates": result.unverifiable,
        "duplicates": duplicate_metrics(result),
        "labels": label_metrics(result),
        "latency_ms": {
            "p50": statistics.median(lat) * 1000 if lat else None,
            "p95": lat[int(0.95 * (len(lat) - 1))] * 1000 if lat else None,
        },
        "tokens_per_issue": per_issue_tokens,
        "est_cost_per_1k_issues_usd": per_issue_tokens * 1000 / 1e6 * JEV_PRICE_PER_M_TOKENS,
    }
