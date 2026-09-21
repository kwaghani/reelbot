from __future__ import annotations

import threading
from typing import Any

from config import settings

MODEL_NAME = settings().embedding_model
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


# Signals worth searching; everything else in raw_signals is provider plumbing.
SIGNAL_KEYS = ("caption", "ocr", "transcript", "hashtags", "creator_name", "user_source_info")
SIGNAL_CHARS = 1200


def signal_text(raw_signals: Any) -> str:
    """Bounded searchable text from the save's own evidence, not provider replies."""
    if not isinstance(raw_signals, dict):
        return ""
    parts: list[str] = []
    for key in SIGNAL_KEYS:
        value = raw_signals.get(key)
        if isinstance(value, list):
            value = " ".join(str(item) for item in value if item)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    import re
    return re.sub(r"\s+", " ", " ".join(parts))[:SIGNAL_CHARS]


def entry_document(row: dict, raw_signals: Any = None) -> str:
    """The one definition of an entry's search text, shared by both indexers."""
    import json
    attributes = row.get("attributes") or {}
    values = []
    for value in attributes.values():
        for item in (value if isinstance(value, list) else [value]):
            if item not in (None, ""):
                values.append(str(item).replace("_", " "))
    parts = [row.get("title"), row.get("summary"), " ".join(values), json.dumps(attributes, sort_keys=True),
             row.get("place_name"), row.get("city"), row.get("note"),
             " ".join(folder["name"] for folder in row.get("folders") or []), signal_text(raw_signals)]
    return " ".join(str(part) for part in parts if part)
