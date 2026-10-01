"""Duplicate detectors for the backtest.

A detector only learns about issues through `add`, which the replay calls after
an issue has been evaluated. It cannot see the future by construction.
"""
import asyncio
import heapq
import math
import re
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Protocol

from sift.jev.base import JevClient
from sift.jev.mock import MockJev
from sift.embeddings import BGEEmbedder, Embedder, strip_attachments
from sift.models import Issue
from sift.similarity import tokens

STOPWORDS = frozenset(
    "the and for with this that from are was were but not you your have has had can could "
    "would should will when what which there their they then than into out use using used "
    "get got all any also just like some more other its it's our been being how why who "
    "does did doesn don didn isn wasn one two after before about issue bug error problem "
    "please thanks thank version expected actual behavior steps reproduce".split()
)

Candidates = list[tuple[Issue, float]]


class DuplicateDetector(Protocol):
    name: str
    tokens_used: int

    def add(self, issue: Issue) -> None: ...

    async def candidates(self, issue: Issue, k: int) -> Candidates: ...

    async def decide(self, issue: Issue, candidates: Candidates) -> tuple[Issue | None, float]:
        """Best duplicate among `candidates` and a confidence in [0, 1]."""
        ...


def content_tokens(text: str) -> frozenset[str]:
    return frozenset(tokens(text) - STOPWORDS)


class TokenIndex:
    """Inverted index for Jaccard search: only scores issues that share a token."""

    def __init__(self) -> None:
        self._docs: dict[int, tuple[Issue, frozenset[str]]] = {}
        self._postings: dict[str, list[int]] = defaultdict(list)

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, issue: Issue) -> None:
        toks = content_tokens(issue.text)
        self._docs[issue.number] = (issue, toks)
        for tok in toks:
            self._postings[tok].append(issue.number)

    def search(self, issue: Issue, k: int) -> Candidates:
        query = content_tokens(issue.text)
        if not query:
            return []
        overlap: Counter[int] = Counter()
        for tok in query:
            for number in self._postings.get(tok, ()):
                overlap[number] += 1
        scored = (
            (n, shared / (len(query) + len(self._docs[n][1]) - shared))
            for n, shared in overlap.items()
        )
        return [(self._docs[n][0], score) for n, score in heapq.nlargest(k, scored, key=lambda x: x[1])]


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


class Boilerplate:
    """Learns issue-template lines from earlier issues and strips them.

    A body line seen in `min_docs` or more earlier issues is template text
    ("Describe what you were doing when the bug occurred"), not content.
    HTML comments are always template instructions.
    """

    def __init__(self, min_docs: int = 3):
        self.min_docs = min_docs
        self._line_docs: Counter[str] = Counter()

    @staticmethod
    def _lines(body: str) -> list[str]:
        return [line.strip().lower() for line in _HTML_COMMENT.sub(" ", body).splitlines()]

    def clean(self, issue: Issue) -> str:
        kept = [line for line in self._lines(issue.body) if self._line_docs[line] < self.min_docs]
        return issue.title + "\n" + "\n".join(kept)

    def add(self, issue: Issue) -> None:
        self._line_docs.update({line for line in self._lines(issue.body) if len(line) >= 8})


class TfIdfIndex:
    """IDF-weighted cosine over word sets, with template text removed.

    IDF is computed from earlier issues only. Document norms are refreshed once
    the index has grown by NORM_REFRESH since they were computed; a norm frozen
    at insert time would make early, short issues match everything.
    """

    MIN_TOKENS = 3
    MAX_DF_RATIO = 0.5  # words in over half of all issues can't tell issues apart
    NORM_REFRESH = 1.1

    def __init__(self) -> None:
        self.boilerplate = Boilerplate()
        self._docs: dict[int, tuple[Issue, frozenset[str]]] = {}
        self._norms: dict[int, tuple[float, int]] = {}  # number -> (norm, index size when computed)
        self._postings: dict[str, list[int]] = defaultdict(list)
        self._df: Counter[str] = Counter()
        self._n = 0  # issues seen, including ones too short to index

    def __len__(self) -> int:
        return self._n

    def _idf(self, tok: str) -> float:
        return math.log((1 + self._n) / (1 + self._df[tok])) + 1.0

    def _norm(self, number: int) -> float:
        cached = self._norms.get(number)
        if cached is None or self._n > cached[1] * self.NORM_REFRESH:
            norm = math.sqrt(sum(self._idf(t) ** 2 for t in self._docs[number][1])) or 1.0
            self._norms[number] = cached = (norm, self._n)
        return cached[0]

    def _tokens(self, issue: Issue) -> frozenset[str]:
        return content_tokens(self.boilerplate.clean(issue))

    def add(self, issue: Issue) -> None:
        toks = self._tokens(issue)
        self._n += 1
        self._df.update(toks)
        self.boilerplate.add(issue)
        if len(toks) < self.MIN_TOKENS:
            return  # nothing to match on; an empty template is not a duplicate target
        self._docs[issue.number] = (issue, toks)
        for tok in toks:
            self._postings[tok].append(issue.number)

    def search(self, issue: Issue, k: int) -> Candidates:
        query = self._tokens(issue)
        if len(query) < self.MIN_TOKENS or not self._docs:
            return []
        weights = {t: self._idf(t) for t in query}
        q_norm = math.sqrt(sum(w * w for w in weights.values()))
        max_df = self.MAX_DF_RATIO * self._n
        acc: defaultdict[int, float] = defaultdict(float)
        for tok, w in weights.items():
            if 0 < self._df[tok] <= max_df:
                for number in self._postings[tok]:
                    acc[number] += w * w
        scored = ((n, min(1.0, s / (q_norm * self._norm(n)))) for n, s in acc.items())
        return [(self._docs[n][0], score) for n, score in heapq.nlargest(k, scored, key=lambda x: x[1])]


class EmbeddingIndex:
    """Cosine search over sentence embeddings, with template text and attachments removed.

    Template lines are learned from earlier issues, as in TfIdfIndex. Each issue is
    embedded once: the vector computed when it is queried is reused when it is added.
    """

    MIN_TOKENS = TfIdfIndex.MIN_TOKENS

    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.boilerplate = Boilerplate()
        self._issues: list[Issue] = []
        self._vecs: Any = None  # numpy (capacity, dim); rows [0, len) are filled
        self._last: tuple[int, Any] | None = None

    def __len__(self) -> int:
        return len(self._issues)

    def text(self, issue: Issue) -> str:
        return self.boilerplate.clean(replace(issue, body=strip_attachments(issue.body)))

    def _embed(self, issue: Issue):
        if self._last is not None and self._last[0] == issue.number:
            return self._last[1]
        text = self.text(issue)
        vec = None if len(content_tokens(text)) < self.MIN_TOKENS else self.embedder.encode([text])[0]
        self._last = (issue.number, vec)
        return vec

    def add(self, issue: Issue) -> None:
        import numpy as np

        vec = self._embed(issue)
        self.boilerplate.add(issue)
        if vec is None:
            return  # an empty template is not a duplicate target
        n = len(self._issues)
        if self._vecs is None or n == len(self._vecs):
            grown = np.zeros((max(1024, 2 * n), len(vec)), dtype=np.float32)
            if self._vecs is not None:
                grown[:n] = self._vecs
            self._vecs = grown
        self._vecs[n] = vec
        self._issues.append(issue)

    def search(self, issue: Issue, k: int) -> Candidates:
        import numpy as np

        vec = self._embed(issue)
        n = len(self._issues)
        if vec is None or n == 0:
            return []
        scores = self._vecs[:n] @ vec
        k = min(k, n)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [(self._issues[i], float(min(1.0, max(0.0, scores[i])))) for i in top]


class TfIdfDetector:
    """Stronger local baseline: the most similar earlier issue by TF-IDF cosine."""

    name = "tfidf"

    def __init__(self) -> None:
        self.index = TfIdfIndex()
        self.tokens_used = 0

    def add(self, issue: Issue) -> None:
        self.index.add(issue)

    async def candidates(self, issue: Issue, k: int) -> Candidates:
        return self.index.search(issue, k)

    async def decide(self, issue: Issue, candidates: Candidates) -> tuple[Issue | None, float]:
        if not candidates:
            return None, 0.0
        best, score = candidates[0]
        return best, score


class JaccardDetector:
    """Naive baseline: the most word-similar earlier issue, scored by Jaccard overlap."""

    name = "jaccard"

    def __init__(self) -> None:
        self.index = TokenIndex()
        self.tokens_used = 0

    def add(self, issue: Issue) -> None:
        self.index.add(issue)

    async def candidates(self, issue: Issue, k: int) -> Candidates:
        return self.index.search(issue, k)

    async def decide(self, issue: Issue, candidates: Candidates) -> tuple[Issue | None, float]:
        if not candidates:
            return None, 0.0
        best, score = candidates[0]
        return best, score


class EmbeddingDetector:
    """Nearest earlier issue by BGE embedding cosine.

    Cosine between issue embeddings is high even for unrelated issues, so the
    confidence sweep is not comparable to TF-IDF's; retrieval (original in top-k)
    is what this detector is for.
    """

    name = "embed"

    def __init__(self, embedder: Embedder):
        self.index = EmbeddingIndex(embedder)
        self.tokens_used = 0

    def add(self, issue: Issue) -> None:
        self.index.add(issue)

    async def candidates(self, issue: Issue, k: int) -> Candidates:
        return self.index.search(issue, k)

    async def decide(self, issue: Issue, candidates: Candidates) -> tuple[Issue | None, float]:
        if not candidates:
            return None, 0.0
        return candidates[0]


class HybridDetector:
    """TF-IDF and embedding candidates merged by reciprocal rank fusion.

    Lexical search catches exact error text and stack traces; embeddings catch
    the same bug described in different words. Candidates keep their TF-IDF
    score (0 if only embeddings found them), so the confidence sweep stays
    comparable to the TF-IDF baseline.
    """

    name = "hybrid"
    RRF_K = 60

    def __init__(self, embedder: Embedder):
        self.lexical = TfIdfIndex()
        self.semantic = EmbeddingIndex(embedder)
        self.tokens_used = 0

    def add(self, issue: Issue) -> None:
        self.lexical.add(issue)
        self.semantic.add(issue)

    async def candidates(self, issue: Issue, k: int) -> Candidates:
        lexical = self.lexical.search(issue, k)
        fused: defaultdict[int, float] = defaultdict(float)
        issues: dict[int, Issue] = {}
        for ranked in (lexical, self.semantic.search(issue, k)):
            for rank, (c, _) in enumerate(ranked):
                fused[c.number] += 1.0 / (self.RRF_K + rank + 1)
                issues[c.number] = c
        lexical_score = {c.number: s for c, s in lexical}
        top = sorted(fused, key=lambda n: (-fused[n], n))[:k]
        return [(issues[n], lexical_score.get(n, 0.0)) for n in top]

    async def decide(self, issue: Issue, candidates: Candidates) -> tuple[Issue | None, float]:
        if not candidates:
            return None, 0.0
        return candidates[0]


class JevDetector:
    """Retrieve-then-decide: top-k from `index` (TF-IDF by default), then Jev judges every pair."""

    def __init__(self, jev: JevClient, name: str, index: TfIdfIndex | EmbeddingIndex | None = None):
        self.name = name
        self.jev = jev
        self.index = index if index is not None else TfIdfIndex()
        self.tokens_used = 0

    def add(self, issue: Issue) -> None:
        self.index.add(issue)

    async def candidates(self, issue: Issue, k: int) -> Candidates:
        return self.index.search(issue, k)

    async def decide(self, issue: Issue, candidates: Candidates) -> tuple[Issue | None, float]:
        if not candidates:
            return None, 0.0
        decisions = await asyncio.gather(*(self.jev.same_issue(issue, c) for c, _ in candidates))
        # ~4 characters per token: both issue texts go into each pairwise prompt.
        self.tokens_used += sum(len(issue.text) + len(c.text) for c, _ in candidates) // 4
        yes = [(c, d.confidence) for (c, _), d in zip(candidates, decisions) if d.answer == "yes"]
        if not yes:
            return None, 0.0
        return max(yes, key=lambda pair: pair[1])


def _local_jev() -> JevClient:
    from sift.jev.local import LocalJev

    return LocalJev()


DETECTORS = {
    "jaccard": JaccardDetector,
    "tfidf": TfIdfDetector,
    "mock-jev": lambda: JevDetector(MockJev(), name="mock-jev"),
    "embed": lambda: EmbeddingDetector(BGEEmbedder()),
    "hybrid": lambda: HybridDetector(BGEEmbedder()),
    "local-jev": lambda: JevDetector(_local_jev(), name="local-jev", index=EmbeddingIndex(BGEEmbedder())),
}
