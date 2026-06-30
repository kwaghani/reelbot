from __future__ import annotations

from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_MODEL = SentenceTransformer(MODEL_NAME, device="cpu")


def embed(text: str) -> list[float]:
    vector = _MODEL.encode(text or "", normalize_embeddings=True)
    return [float(value) for value in vector.tolist()]
