"""Local sentence embeddings for duplicate retrieval (BGE, matches the vector(384) column).

Install with: pip install -e '.[embeddings]'
"""
import re
from collections.abc import Sequence
from typing import Any, Protocol

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384

# Collapsed blocks are attachments, not prose: VS Code's system-info table, logs, etc.
# Left in, they fill the model's 512-token window and make every report look alike.
_DETAILS = re.compile(r"<details>.*?</details>", re.S | re.I)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def strip_attachments(body: str) -> str:
    return _DETAILS.sub(" ", _HTML_COMMENT.sub(" ", body))


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]) -> Any:
        """Unit-length vectors, one row per text (numpy array of shape (n, dim))."""
        ...


class BGEEmbedder:
    """BGE small, loaded on first use. Vectors are normalized, so dot product = cosine."""

    def __init__(self, model_name: str = MODEL_NAME, device: str | None = None):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def encode(self, texts: Sequence[str]):
        return self._load().encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
