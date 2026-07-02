from __future__ import annotations

import logging
import os
import re
import tempfile
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
        return f"Could not process this reel during {exc.stage}: {message[:260]}"
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
        item.get("place_name"),
        item.get("category"),
        item.get("location_text"),
        item.get("price_tier"),
        " ".join(tags),
        (item.get("transcript") or "")[:500],
    ]
    return "\n".join(str(part) for part in parts if part)


def fallback_list_name(item: dict[str, Any]) -> str:
    location = item.get("location_text")
    category = item.get("category")
    if location and category:
        return f"{location} {category}"[:40]
    return str(location or category or "Saved places")[:40]


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
Name the lightweight shared list this saved place belongs in.
Return only a short natural list name, 1 to 4 words.
Examples: Bali, LA food, date ideas, coffee, hikes.

Place: {item.get("place_name")}
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

    if not result.get("has_place"):
        mark_job_done(conn, job["id"], "Couldn't find a place in that one 🤔")
        return

    place_id = result.get("place_id")
    if not place_id:
        raise RuntimeError("Verified reel did not include a place_id")

    list_name = assign_list_name(result)
    embedding = embed(synthesize_embedding_text(result))
    member = get_or_create_member(conn, group_id, str(job["sender_id"]))
    item = upsert_item(
        conn,
        group_id=group_id,
        source_url=str(job["payload"]),
        place_id=str(place_id),
        place_name=result.get("place_name"),
        category=result.get("category"),
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
    mark_job_done(conn, job["id"], f"Saved → {item.get('place_name') or result.get('place_name')} ({final_list})")


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


def main() -> int:
    load_dotenv(ROOT / ".env")
    LOG.info("Starting worker")

    with connect() as conn:
        while True:
            job = claim_next_job(conn)
            if job is None:
                time.sleep(POLL_SECONDS)
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


if __name__ == "__main__":
    raise SystemExit(main())
