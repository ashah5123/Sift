def _pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value * 100:5.1f}%"


def format_report(repo: str, s: dict) -> str:
    d = s["duplicates"]
    recall_key = next(key for key in d if key.startswith("recall_at_"))
    k = recall_key.removeprefix("recall_at_")
    lines = [
        f"Backtest: {repo}  detector={s['detector']}",
        f"  evaluated issues        {s['evaluated']}",
        f"  scored for duplicates   {d['scored_issues']}  "
        f"(known duplicates: {d['known_duplicates']}, "
        f"skipped with unknown original: {s['unverifiable_duplicates']})",
        f"  original in top-{k:<3}      {_pct(d[recall_key])}",
        "",
        "  Duplicate detection by confidence threshold",
        "  threshold  flagged  precision  recall  false-action rate",
    ]
    for row in d["sweep"]:
        lines.append(
            f"  {row['threshold']:>9.1f}  {row['flagged']:>7}  {_pct(row['precision']):>9}  "
            f"{_pct(row['recall']):>6}  {_pct(row['false_action_rate']):>17}"
        )
    if s["labels"]:
        lab = s["labels"]
        lines += [
            "",
            "  Labels",
            f"  labeled issues          {lab['labeled_issues']}",
            f"  coverage                {_pct(lab['coverage'])}",
            f"  accuracy when answered  {_pct(lab['accuracy_when_answered'])}",
            f"  accuracy overall        {_pct(lab['accuracy_overall'])}",
            f"  most-frequent baseline  {_pct(lab['most_frequent_baseline'])}",
        ]
    lat = s["latency_ms"]
    lines += [
        "",
        f"  latency p50 / p95       {lat['p50'] or 0:.2f} ms / {lat['p95'] or 0:.2f} ms",
        f"  est. cost per 1k issues ${s['est_cost_per_1k_issues_usd']:.4f}",
    ]
    return "\n".join(lines)
