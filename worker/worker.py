from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from db import (
    add_item_save,
    claim_next_job,
    connect,
    get_or_create_member,
    log_event,
    mark_job_done,
    mark_job_error,
    requeue_retryable_ingest_errors,
    upsert_item,
)
from embed import embed
from pipeline import StageError, process_reel
from retrieval import answer_question

ROOT = Path(__file__).resolve().parents[1]
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
POLL_SECONDS = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("reelbot.worker")


def job_error_reply(exc: Exception) -> str:
    if isinstance(exc, StageError):
        message = re.sub(r"\s+", " ", exc.message).strip()
        return f"Could not process this reel during {exc.stage}: {message[:220]} Metadata fallback was tried."
    if isinstance(exc, MemoryError):
        return "Could not process this reel because the worker ran out of memory."
    return "I hit a snag processing that one, but I am still running."


def response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif isinstance(block, dict) and block.get("text") is not None:
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def synthesize_embedding_text(item: dict[str, Any]) -> str:
    tags = item.get("tags") or []
    parts = [
        item.get("place_name") or item.get("title"),
        item.get("content_type"),
        item.get("category"),
        item.get("location_text"),
        item.get("price_tier"),
        " ".join(tags),
        (item.get("caption") or "")[:300],
        (item.get("transcript") or "")[:500],
    ]
    return "\n".join(str(part) for part in parts if part)


def fallback_list_name(item: dict[str, Any]) -> str:
    location = item.get("location_text")
    category = item.get("category") or item.get("content_type")
    if location and category:
        return f"{location} {category}"[:40]
    return str(location or category or "Saved items")[:40]


def clean_list_name(text: str) -> str:
    text = text.strip().strip("\"'")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^A-Za-z0-9 &'/-]", "", text)
    return text[:40].strip() or "Saved places"


def assign_list_name(item: dict[str, Any]) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return fallback_list_name(item)

    prompt = f"""
Name the folder this saved item belongs in.
Return only a short natural folder name, 1 to 4 words.
Examples: Bali, LA food, date ideas, recipes, workouts, coffee, hikes, fits, gadgets.

Title: {item.get("title") or item.get("place_name")}
Type: {item.get("content_type")}
Location: {item.get("location_text")}
Category: {item.get("category")}
Tags: {", ".join(item.get("tags") or [])}
Transcript: {(item.get("transcript") or "")[:220]}
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=40,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return clean_list_name(response_text(response))
    except Exception as exc:
        LOG.warning("List-name assignment failed: %s", exc)
        return fallback_list_name(item)


def handle_ingest(conn, job: dict[str, Any]) -> None:
    group_id = job.get("group_id")
    if not group_id:
        raise RuntimeError("Ingest job is missing group_id")

    with tempfile.TemporaryDirectory(prefix="reelbot-") as workdir:
        result = process_reel(str(job["payload"]), workdir)

    if not result.get("has_content") and not result.get("has_place"):
        mark_job_done(conn, job["id"], "Couldn't find anything to save in that one 🤔")
        return

    display_name = result.get("place_name") or result.get("title")
    if not display_name:
        mark_job_done(conn, job["id"], "Couldn't find anything to save in that one 🤔")
        return

    # Non-place content has no Google place_id; dedupe those by reel/URL instead.
    place_id = result.get("place_id") or f"content_{result.get('reel_id')}"

    list_name = assign_list_name(result)
    embedding = embed(synthesize_embedding_text(result))
    member = get_or_create_member(conn, group_id, str(job["sender_id"]))
    item = upsert_item(
        conn,
        group_id=group_id,
        source_url=str(job["payload"]),
        place_id=str(place_id),
        place_name=display_name,
        category=result.get("category") or result.get("content_type"),
        location_text=result.get("location_text"),
        lat=result.get("lat"),
        lng=result.get("lng"),
        price_tier=result.get("price_tier"),
        tags=result.get("tags") or [],
        list_name=list_name,
        transcript=result.get("transcript"),
        embedding=embedding,
    )
    add_item_save(conn, item["id"], member["id"])

    final_list = item.get("list_name") or list_name
    log_event(conn, group_id, "save", str(item["id"]))
    mark_job_done(conn, job["id"], f"Saved → {item.get('place_name') or display_name} ({final_list})")


def handle_query(conn, job: dict[str, Any]) -> None:
    group_id = job.get("group_id")
    if not group_id:
        raise RuntimeError("Query job is missing group_id")

    answer = answer_question(group_id, str(job["payload"]))
    log_event(conn, group_id, "query", str(job["id"]))
    mark_job_done(conn, job["id"], answer)


def handle_job(conn, job: dict[str, Any]) -> None:
    job_type = str(job.get("type") or "")
    LOG.info("Processing %s job %s", job_type, job.get("id"))
    if job_type == "ingest":
        handle_ingest(conn, job)
    elif job_type == "query":
        handle_query(conn, job)
    else:
        raise RuntimeError(f"Unknown job type: {job_type}")


def job_loop(
    *,
    only_type: str | None = None,
    exclude_type: str | None = None,
    poll_seconds: float = POLL_SECONDS,
    requeue_errors: bool = False,
) -> None:
    with connect() as conn:
        while True:
            if requeue_errors:
                requeue_retryable_ingest_errors(conn)
            job = claim_next_job(conn, only_type=only_type, exclude_type=exclude_type)
            if job is None:
                time.sleep(poll_seconds)
                continue

            try:
                handle_job(conn, job)
            except Exception as exc:
                LOG.exception("Job %s failed", job.get("id"))
                conn.rollback()
                try:
                    mark_job_error(conn, job["id"], job_error_reply(exc))
                    log_event(conn, job.get("group_id"), "error", f"{type(exc).__name__}: {exc}")
                except Exception:
                    LOG.exception("Could not record failure for job %s", job.get("id"))


def main() -> int:
    load_dotenv(ROOT / ".env")
    LOG.info("Starting worker")

    # Queries get their own thread so a slow ingest (audio download +
    # transcription) never blocks an interactive answer.
    query_thread = threading.Thread(
        target=job_loop,
        kwargs={"only_type": "query", "poll_seconds": 0.5},
        daemon=True,
        name="query-jobs",
    )
    query_thread.start()

    job_loop(exclude_type="query", requeue_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
