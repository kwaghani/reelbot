from __future__ import annotations

from typing import Any

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_MODEL: Any | None = None


def get_model() -> Any:
    global _MODEL

    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer(MODEL_NAME, device="cpu")
    return _MODEL


def embed(text: str) -> list[float]:
    vector = get_model().encode(text or "", normalize_embeddings=True)
    return [float(value) for value in vector.tolist()]
