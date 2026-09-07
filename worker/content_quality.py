from __future__ import annotations

import re
from collections import Counter


TOKEN_RE = re.compile(r"[a-z']+")
CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
MAX_CHUNK_TOKENS = 32


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _transcript_chunks(text: str | None) -> list[str]:
    """Keep natural transcript boundaries and bound unusually long lines."""
    chunks: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for clause in CLAUSE_SPLIT_RE.split(line):
            clause = clause.strip()
            words = clause.split()
            if len(words) <= MAX_CHUNK_TOKENS:
                if clause:
                    chunks.append(clause)
                continue
            chunks.extend(
                " ".join(words[index : index + MAX_CHUNK_TOKENS])
                for index in range(0, len(words), MAX_CHUNK_TOKENS)
            )
    return chunks


def looks_like_repetitive_lyrics(line: str) -> bool:
    """Flag repeated first-person chorus-like text without topic assumptions."""
    tokens = _tokens(line)
    if len(tokens) < 24:
        return False
    first_person = sum(token in {"i", "i'm", "i'll", "i've", "we", "you"} for token in tokens)
    if first_person < 5:
        return False
    four_grams = Counter(tuple(tokens[index : index + 4]) for index in range(len(tokens) - 3))
    return bool(four_grams and max(four_grams.values()) >= 2)


def sanitize_transcript(text: str | None) -> str:
    chunks = _transcript_chunks(text)
    if not chunks:
        return ""

    chunk_tokens = [_tokens(chunk) for chunk in chunks]
    four_grams = Counter(
        tuple(tokens[index : index + 4])
        for tokens in chunk_tokens
        for index in range(len(tokens) - 3)
    )
    repeated = {gram for gram, count in four_grams.items() if count >= 2}

    kept: list[str] = []
    for chunk, tokens in zip(chunks, chunk_tokens):
        first_person = sum(token in {"i", "i'm", "i'll", "i've", "we", "you"} for token in tokens)
        contains_repeat = any(
            tuple(tokens[index : index + 4]) in repeated
            for index in range(len(tokens) - 3)
        )
        repeated_lyric_chunk = len(tokens) >= 8 and first_person >= 2 and contains_repeat
        if not repeated_lyric_chunk and not looks_like_repetitive_lyrics(chunk):
            kept.append(chunk)
    return "\n".join(kept)


def answerable_content(text: str | None) -> str:
    """Prefer structured evidence and keep raw background noise out of answers."""
    content = str(text or "").strip()
    marker = "Original reel content:"
    if content.startswith("Summary:") and marker in content:
        content = content.split(marker, 1)[0].strip()
    return sanitize_transcript(content)
