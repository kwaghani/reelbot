from __future__ import annotations

import json
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
from retrieval import answer_question_structured, plain_answer_with_sources

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


TOP_FOLDERS = [
    "Restaurants",
    "Cafes & Desserts",
    "Bars & Nightlife",
    "Recipes",
    "Workouts",
    "Travel",
    "Things To Do",
    "Shopping",
    "Fashion",
    "Tech",
    "Humor",
    "Ideas",
    "Other",
]


CANONICAL_SUBFOLDERS = {
    "new york": "NYC",
    "new york city": "NYC",
    "nyc": "NYC",
    "la": "Los Angeles",
    "l a": "Los Angeles",
    "los angeles": "Los Angeles",
    "bombay": "Mumbai",
    "sf": "San Francisco",
}


def clean_folder_name(text: Any) -> str:
    text = str(text or "").strip().strip("\"'")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^A-Za-z0-9 &'/-]", "", text)
    text = text[:32].strip()
    return CANONICAL_SUBFOLDERS.get(text.lower().replace(".", ""), text)


def fallback_folders(item: dict[str, Any]) -> tuple[str, str | None]:
    content_type = str(item.get("content_type") or "").strip().lower()
    mapping = {
        "place": "Travel",
        "restaurant": "Restaurants",
        "recipe": "Recipes",
        "workout": "Workouts",
        "travel": "Travel",
        "fashion": "Fashion",
        "product": "Shopping",
        "tech": "Tech",
        "meme": "Humor",
    }
    folder = mapping.get(content_type, str(item.get("category") or "Other").title()[:32])
    location = str(item.get("location_text") or "").strip()
    subfolder = clean_folder_name(location.split(",")[0]) if location else None
    return folder or "Other", subfolder or None


def assign_folders(item: dict[str, Any]) -> tuple[str, str | None]:
    """Pick a broad top-level folder and an optional narrower subfolder."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return fallback_folders(item)

    prompt = f"""
File this saved item into a two-level folder structure.
Return ONLY a JSON object: {{"folder": str, "subfolder": str|null}}

folder is the BROAD top-level bucket. Prefer one of:
{", ".join(TOP_FOLDERS)}
Only invent a new folder when none fit, and keep it just as broad
(1-2 plural words). Never put a city, dish, or other specifics in folder.

subfolder is the narrower group inside the folder:
- for places: the city or well-known area, e.g. "Brooklyn", "Munich", "Los Angeles"
- for recipes: the kind of dish, e.g. "Pasta", "Desserts"
- for workouts: the focus, e.g. "Full Body", "Chest"
- null when nothing natural fits.
Bad example: folder "Brooklyn Restaurants". Good: folder "Restaurants", subfolder "Brooklyn".

Title: {item.get("title") or item.get("place_name")}
Type: {item.get("content_type")}
Location: {item.get("location_text")}
Category: {item.get("category")}
Tags: {", ".join(item.get("tags") or [])}
Content: {(item.get("transcript") or "")[:220]}
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=100,
            temperature=0,
            system="Return JSON only. No markdown, no prose.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response_text(response)
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start : end + 1])
        folder = clean_folder_name(parsed.get("folder"))
        subfolder = clean_folder_name(parsed.get("subfolder")) or None
        if folder:
            return folder, subfolder
    except Exception as exc:
        LOG.warning("Folder assignment failed: %s", exc)
    return fallback_folders(item)


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

    # Keep the caption, transcript, and on-screen text together: answers are
    # only as good as what gets stored here.
    content_text = "\n".join(
        part.strip()
        for part in [result.get("caption"), result.get("transcript"), result.get("ocr_text")]
        if part and str(part).strip()
    )[:2000]

    folder, subfolder = assign_folders(result)
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
        list_name=folder,
        subfolder=subfolder,
        transcript=content_text or None,
        embedding=embedding,
    )
    add_item_save(conn, item["id"], member["id"])

    final_list = item.get("list_name") or folder
    final_sub = item.get("subfolder") or subfolder
    label = f"{final_list} › {final_sub}" if final_sub else final_list
    log_event(conn, group_id, "save", str(item["id"]))
    mark_job_done(conn, job["id"], f"Saved → {item.get('place_name') or display_name} ({label})")


def handle_query(conn, job: dict[str, Any]) -> None:
    group_id = job.get("group_id")
    if not group_id:
        raise RuntimeError("Query job is missing group_id")

    structured = answer_question_structured(group_id, str(job["payload"]))
    if str(job.get("chat_id") or "") == "app":
        # The API decodes this envelope into {answer, sources} for the app.
        reply = json.dumps(structured, ensure_ascii=False)
    else:
        reply = plain_answer_with_sources(structured)
    log_event(conn, group_id, "query", str(job["id"]))
    mark_job_done(conn, job["id"], reply)


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
