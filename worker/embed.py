from __future__ import annotations

import os
import threading
from typing import Any

from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5").strip() or "BAAI/bge-small-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_MODEL: Any | None = None
_MODEL_LOCK = threading.Lock()


def get_model() -> Any:
    global _MODEL

    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                from sentence_transformers import SentenceTransformer
                _MODEL = SentenceTransformer(MODEL_NAME, device="cpu")
    return _MODEL


def _embed(text: str) -> list[float]:
    vector = get_model().encode(text or "", normalize_embeddings=True)
    return [float(value) for value in vector.tolist()]


def embed_document(text: str) -> list[float]:
    return _embed(text)


def embed_query(text: str) -> list[float]:
    if "bge-" in MODEL_NAME.lower():
        text = QUERY_INSTRUCTION + (text or "")
    return _embed(text)


def embed(text: str) -> list[float]:
    """Backward-compatible alias for document embeddings."""
    return embed_document(text)
