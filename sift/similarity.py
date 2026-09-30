"""Text similarity with no dependencies. Baseline for duplicate retrieval until BGE + pgvector."""
import re

_TOKEN = re.compile(r"[a-z0-9_]{3,}")


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
