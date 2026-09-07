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
    group_folders,
    log_event,
    mark_job_done,
    mark_job_error,
    requeue_retryable_ingest_errors,
    upsert_item,
)
from reel_urls import canonical_reel_url, content_identity
from embed import embed_document
from pipeline import StageError, process_reel
from retrieval import answer_question_structured, plain_answer_with_sources

ROOT = Path(__file__).resolve().parents[1]
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"
POLL_SECONDS = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("reelbot.worker")


def job_error_reply(exc: Exception) -> str:
    if isinstance(exc, StageError):
        if exc.stage == "ingest":
            return "Could not read this reel. It may be private, removed, or temporarily unavailable. Open the source and try again."
        return f"Could not process this reel during {exc.stage}. Please try again later."
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


def synthesize_embedding_text(
    item: dict[str, Any],
    folder: str | None = None,
    subfolder: str | None = None,
) -> str:
    tags = item.get("tags") or []
    key_details = item.get("key_details") or []
    parts = [
        item.get("place_name") or item.get("title"),
        item.get("content_type"),
        item.get("category"),
        folder or item.get("list_name"),
        subfolder or item.get("subfolder"),
        item.get("location_text"),
        item.get("price_tier"),
        " ".join(tags),
        item.get("summary"),
        " ".join(str(detail) for detail in key_details),
        (item.get("caption") or "")[:300],
        (item.get("transcript") or "")[:900],
        (item.get("ocr_text") or "")[:400],
    ]
    return "\n".join(str(part) for part in parts if part)


def build_stored_content(item: dict[str, Any]) -> str:
    """Put model-extracted facts before noisy captions/OCR for answer quality."""
    sections: list[str] = []
    summary = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
    if summary:
        sections.append(f"Summary: {summary}")

    details = [
        re.sub(r"\s+", " ", str(detail)).strip()
        for detail in item.get("key_details") or []
        if str(detail).strip()
    ]
    if details:
        sections.append("Key details:\n" + "\n".join(f"- {detail}" for detail in details[:12]))

    raw_parts: list[str] = []
    seen: set[str] = set()
    for value in [item.get("caption"), item.get("transcript"), item.get("ocr_text")]:
        clean = str(value or "").strip()
        normalized = re.sub(r"\s+", " ", clean).lower()
        if clean and normalized not in seen:
            seen.add(normalized)
            raw_parts.append(clean)
    if raw_parts:
        sections.append("Original reel content:\n" + "\n".join(raw_parts))
    return "\n\n".join(sections)[:5000]


# Broad, durable buckets. These are examples of the right altitude, not a
# closed list — the model is free to create an equally-broad new folder
# (e.g. "Gardening") when a save genuinely fits none of these.
TOP_FOLDERS = [
    "Restaurants",
    "Cafes & Desserts",
    "Bars & Nightlife",
    "Recipes",
    "Workouts",
    "Sports",
    "Travel",
    "Things To Do",
    "Outdoors",
    "Relationships & Dating",
    "Comedy & Memes",
    "Shopping & Products",
    "Fashion & Style",
    "Beauty & Skincare",
    "Home & DIY",
    "Tech & Gadgets",
    "Cars & Bikes",
    "Movies & TV",
    "Music",
    "Gaming",
    "Pets & Animals",
    "Books & Reading",
    "Learning & How-To",
    "Health & Wellness",
    "Motivation & Mindset",
    "Money & Career",
    "Creative Ideas",
    "Other",
]


FOLDER_ALIASES = {
    "humor": "Comedy & Memes",
    "memes": "Comedy & Memes",
    "comedy": "Comedy & Memes",
    "relationships": "Relationships & Dating",
    "dating": "Relationships & Dating",
    "shopping": "Shopping & Products",
    "products": "Shopping & Products",
    "fashion": "Fashion & Style",
    "beauty": "Beauty & Skincare",
    "home & decor": "Home & DIY",
    "home": "Home & DIY",
    "tech": "Tech & Gadgets",
    "cars": "Cars & Bikes",
    "learning": "Learning & How-To",
    "finance": "Money & Career",
    "ideas": "Creative Ideas",
}


FOLDER_KEYWORDS = {
    "Relationships & Dating": (
        "relationship", "dating", "date advice", "couple", "boyfriend", "girlfriend",
        "husband", "wife", "flirt", "pickup line", "pick up line", "romance advice",
    ),
    "Comedy & Memes": ("comedy", "comedian", "meme", "funny", "skit", "parody", "joke"),
    "Restaurants": ("restaurant", "pizzeria", "dining", "brunch", "lunch", "dinner", "food spot"),
    "Cafes & Desserts": ("cafe", "coffee", "bakery", "dessert", "donut", "pastry", "ice cream"),
    "Bars & Nightlife": ("bar", "cocktail", "nightlife", "club", "pub", "brewery"),
    "Recipes": ("recipe", "ingredient", "cook", "pasta", "bake", "meal prep"),
    "Workouts": ("workout", "fitness", "exercise", "gym", "cardio", "hiit", "chest", "leg day"),
    "Sports": ("sports", "football", "soccer", "basketball", "nba", "nfl", "fifa", "cricket", "tennis"),
    "Outdoors": ("hike", "hiking", "trail", "camping", "outdoors", "nature", "beach", "waterfall"),
    "Things To Do": ("things to do", "activity", "activities", "exhibit", "museum", "event", "attraction"),
    "Travel": ("travel", "destination", "itinerary", "vacation", "holiday", "hotel", "flight", "tour"),
    "Fashion & Style": ("fashion", "outfit", "style", "fit check", "clothes", "sneakers"),
    "Beauty & Skincare": ("beauty", "skincare", "makeup", "hair", "cosmetic"),
    "Shopping & Products": ("product", "shopping", "buy", "amazon find", "review", "unboxing"),
    "Home & DIY": ("home decor", "interior", "diy", "craft", "woodworking", "renovation"),
    "Tech & Gadgets": ("tech", "gadget", "software", "phone", "laptop", "app", "camera"),
    "Cars & Bikes": ("car", "cars", "automotive", "motorcycle", "bike", "vehicle"),
    "Movies & TV": ("movie", "film", "tv show", "series", "actor", "trailer"),
    "Music": ("music", "song", "album", "concert", "singer", "artist"),
    "Gaming": ("gaming", "video game", "gameplay", "xbox", "playstation", "nintendo"),
    "Pets & Animals": ("pet", "dog", "cat", "animal", "puppy", "kitten"),
    "Books & Reading": ("book", "reading", "novel", "author", "booktok"),
    "Learning & How-To": ("tutorial", "how to", "explainer", "lesson", "study", "educational"),
    "Health & Wellness": ("health", "wellness", "mental health", "nutrition", "sleep", "meditation"),
    "Motivation & Mindset": ("motivation", "mindset", "discipline", "self improvement", "inspiration"),
    "Money & Career": ("finance", "money", "investing", "career", "job", "business", "productivity"),
    "Creative Ideas": ("idea", "inspiration", "photography", "design", "art", "creative"),
}


CANONICAL_SUBFOLDERS = {
    "new york": "NYC",
    "new york city": "NYC",
    "nyc": "NYC",
    "la": "Los Angeles",
    "l a": "Los Angeles",
    "los angeles": "Los Angeles",
    "bombay": "Mumbai",
    "sf": "San Francisco",
    "soccer": "Football",
}


def clean_folder_name(text: Any) -> str:
    text = str(text or "").strip().strip("\"'")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^A-Za-z0-9 &'/-]", "", text)
    text = text[:32].strip()
    return CANONICAL_SUBFOLDERS.get(text.lower().replace(".", ""), text)


def canonical_folder_name(text: Any) -> str:
    cleaned = clean_folder_name(text)
    return FOLDER_ALIASES.get(cleaned.lower(), cleaned)


def item_classification_text(item: dict[str, Any]) -> str:
    values = [
        item.get("title"),
        item.get("place_name"),
        item.get("content_type"),
        item.get("category"),
        item.get("summary"),
        " ".join(str(tag) for tag in item.get("tags") or []),
        " ".join(str(detail) for detail in item.get("key_details") or []),
        str(item.get("transcript") or "")[:800],
    ]
    return " ".join(str(value) for value in values if value).lower()


def location_subfolder(value: Any) -> str | None:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if not parts:
        return None
    if re.search(r"\d", parts[0]) and len(parts) > 1:
        parts = parts[1:]
    for part in parts:
        if re.fullmatch(r"[A-Za-z]{2}\s+\d{4,6}", part) or re.fullmatch(r"\d{4,6}", part):
            continue
        cleaned = clean_folder_name(part)
        if cleaned:
            return cleaned
    return None


def contains_keyword(text: str, keyword: str) -> bool:
    """Match a folder keyword as words, not inside unrelated words."""
    phrase = r"\s+".join(re.escape(part) for part in keyword.lower().split())
    return bool(re.search(rf"(?<![a-z0-9]){phrase}(?![a-z0-9])", text.lower()))


def fallback_folders(item: dict[str, Any]) -> tuple[str, str | None]:
    content_type = str(item.get("content_type") or "").strip().lower()
    direct_mapping = {
        "restaurant": "Restaurants",
        "recipe": "Recipes",
        "workout": "Workouts",
        "sport": "Sports",
        "sports": "Sports",
        "travel": "Travel",
        "fashion": "Fashion & Style",
        "beauty": "Beauty & Skincare",
        "product": "Shopping & Products",
        "tech": "Tech & Gadgets",
        "car": "Cars & Bikes",
        "music": "Music",
        "movie": "Movies & TV",
        "gaming": "Gaming",
        "game": "Gaming",
        "pet": "Pets & Animals",
        "animal": "Pets & Animals",
        "book": "Books & Reading",
        "finance": "Money & Career",
        "meme": "Comedy & Memes",
    }
    folder = direct_mapping.get(content_type)
    text = item_classification_text(item)
    if folder is None or content_type in {"place", "travel", "advice", "other"}:
        scores = {
            candidate: sum(1 for keyword in keywords if contains_keyword(text, keyword))
            for candidate, keywords in FOLDER_KEYWORDS.items()
        }
        best, score = max(scores.items(), key=lambda pair: pair[1])
        if score:
            folder = best
    folder = canonical_folder_name(folder or item.get("category") or "Other")

    subfolder = None
    if folder in {"Restaurants", "Cafes & Desserts", "Bars & Nightlife", "Travel", "Things To Do", "Outdoors"}:
        subfolder = location_subfolder(item.get("location_text"))
    elif folder == "Relationships & Dating":
        if "pickup line" in text or "pick up line" in text:
            subfolder = "Pickup Lines"
        elif "couple" in text or "relationship" in text:
            subfolder = "Couple Stuff"
    elif folder == "Sports":
        for label, terms in [
            ("Football", ("football", "soccer", "fifa")),
            ("Basketball", ("basketball", "nba")),
            ("Cricket", ("cricket",)),
            ("Tennis", ("tennis",)),
        ]:
            if any(term in text for term in terms):
                subfolder = label
                break
    elif folder == "Recipes":
        for label in ("Pasta", "Desserts", "Breakfast", "Dinner"):
            if label.lower() in text:
                subfolder = label
                break
    return folder or "Other", subfolder or None


def match_existing_folder(name: str, existing: list[str]) -> str:
    """Snap to an existing folder that only differs by case/spacing/punctuation
    so we don't spawn 'Sports' next to 'sports'."""
    name = canonical_folder_name(name)
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    for candidate in existing:
        canonical_candidate = canonical_folder_name(candidate)
        if re.sub(r"[^a-z0-9]", "", canonical_candidate.lower()) == normalized:
            return canonical_candidate
    return name


def assign_folders(
    item: dict[str, Any],
    existing_folders: list[dict[str, Any]] | None = None,
) -> tuple[str, str | None]:
    """Pick a broad top-level folder and an optional narrower subfolder,
    reusing the group's existing folders when the save fits one."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return fallback_folders(item)

    existing_folders = existing_folders or []
    existing_names = [str(row.get("folder")) for row in existing_folders if row.get("folder")]
    if existing_names:
        existing_lines = "\n".join(
            f"- {row['folder']}"
            + (f" (has: {', '.join(str(s) for s in row.get('subfolders') or [])})" if row.get("subfolders") else "")
            for row in existing_folders
        )
        existing_block = (
            "This group already uses these folders. Reuse the one that fits so "
            "related saves stay together:\n" + existing_lines + "\n\n"
        )
    else:
        existing_block = ""

    prompt = f"""
File this saved item into a two-level folder structure that feels natural and
relatable to the people who saved it.
Return ONLY JSON: {{"folder": str, "subfolder": str|null, "confidence": number}}

{existing_block}folder is the BROAD topic bucket — what KIND of thing this is
(e.g. a football highlight is Sports; a cooking video is Recipes; a funny
skit is Comedy & Memes). Common buckets: {", ".join(TOP_FOLDERS)}.
Rules for folder:
- If one of the group's existing folders fits, reuse its exact name.
- Otherwise pick the best matching common bucket above.
- If NOTHING above genuinely fits, CREATE a new broad folder named for the
  topic (1-2 words, plural, e.g. "Sports", "Gardening", "Concerts"). A weak
  fit like dumping everything into Comedy & Memes or Other is wrong — prefer a
  real new folder. Only use Comedy & Memes for actually funny/meme content, and Other
  only as a true last resort.
- Relationships, dating advice, couple moments, flirting, and pickup lines go
  in Relationships & Dating — not Comedy & Memes just because they are funny
  or relatable. Use Comedy & Memes only when entertainment is the main point.
- Travel means destination guides, itineraries, hotels, and travel logistics.
  A local activity goes in Things To Do; hikes/nature go in Outdoors.
- Pick the folder where a person would actually look for this again. Classify
  the main purpose, not a passing joke, background song, or visual style.
- folder never contains a city, dish, team, creator, or other specifics.

subfolder is the narrower group inside the folder:
- places: the city or area, e.g. "Brooklyn", "Munich"
- recipes: the dish type, e.g. "Pasta", "Desserts"
- workouts: the focus, e.g. "Full Body", "Chest"
- sports: the sport or league, e.g. "Football", "NBA"
- relationships: the use, e.g. "Pickup Lines", "Date Ideas", "Couple Stuff"
- comedy: the format, e.g. "Memes", "Sketches", "Stand-Up"
- null when nothing natural fits.
Bad: folder "Brooklyn Restaurants". Good: folder "Restaurants", subfolder "Brooklyn".

Title: {item.get("title") or item.get("place_name")}
Type: {item.get("content_type")}
Location: {item.get("location_text")}
Category: {item.get("category")}
Tags: {", ".join(item.get("tags") or [])}
Summary: {item.get("summary")}
Key details: {"; ".join(str(detail) for detail in item.get("key_details") or [])}
Content: {(item.get("transcript") or item.get("caption") or "")[:900]}
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=160,
            system="Return JSON only. No markdown, no prose.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response_text(response)
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start : end + 1])
        folder = canonical_folder_name(parsed.get("folder"))
        subfolder = clean_folder_name(parsed.get("subfolder")) or None
        confidence = float(parsed.get("confidence", 0.75))
        if folder and confidence >= 0.5:
            return match_existing_folder(folder, existing_names), subfolder
    except Exception as exc:
        LOG.warning("Folder assignment failed: %s", exc)
    return fallback_folders(item)


def handle_ingest(conn, job: dict[str, Any]) -> None:
    group_id = job.get("group_id")
    if not group_id:
        raise RuntimeError("Ingest job is missing group_id")

    url = canonical_reel_url(str(job["payload"]))
    with tempfile.TemporaryDirectory(prefix="reelbot-") as workdir:
        result = process_reel(url, workdir)

    if not result.get("has_content") and not result.get("has_place"):
        mark_job_error(conn, job["id"], "No readable reel content was found. Open the source to check availability, then try sharing again.")
        return

    display_name = result.get("place_name") or result.get("title")
    if not display_name:
        mark_job_error(conn, job["id"], "No readable reel content was found. Open the source to check availability, then try sharing again.")
        return

    # Non-place content has no Google place_id; dedupe those by reel/URL instead.
    place_id = result.get("place_id") or content_identity(result.get("source_url") or url)

    # Keep the caption, transcript, and on-screen text together: answers are
    # only as good as what gets stored here.
    content_text = build_stored_content(result)

    folder, subfolder = assign_folders(result, group_folders(conn, group_id))
    embedding = embed_document(synthesize_embedding_text(result, folder, subfolder))
    # Keep provider/ML work outside the write transaction. Serialize final
    # group mutations so deletion and concurrent copies cannot resurrect data.
    conn.commit()
    conn.execute("select id from groups where id = %s for update", (group_id,))
    current = conn.execute("select status from jobs where id = %s for update", (job["id"],)).fetchone()
    if not current or current["status"] != "processing":
        return
    if str(job.get("chat_id")) == "app":
        member = conn.execute("select * from members where group_id = %s and wa_user_id = %s", (group_id, str(job["sender_id"]))).fetchone()
        if member is None:
            mark_job_error(conn, job["id"], "You no longer have access to this group.")
            return
    else:
        member = get_or_create_member(conn, group_id, str(job["sender_id"]))
    # Reuse a prior source even if provider metadata/verification changed.
    existing = conn.execute("select place_id from items where group_id = %s and source_url = %s order by created_at limit 1", (group_id, url)).fetchone()
    if existing and existing.get("place_id"):
        place_id = existing["place_id"]
    item = upsert_item(
        conn,
        group_id=group_id,
        source_url=url,
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
    conn.execute("update jobs set item_id = %s where id = %s", (item["id"], job["id"]))

    final_list = item.get("list_name") or folder
    final_sub = item.get("subfolder") or subfolder
    label = f"{final_list} › {final_sub}" if final_sub else final_list
    log_event(conn, group_id, "save", str(item["id"]))
    mark_job_done(conn, job["id"], f"Saved → {item.get('place_name') or display_name} ({label})")


def handle_query(conn, job: dict[str, Any]) -> None:
    group_id = job.get("group_id")
    if not group_id:
        raise RuntimeError("Query job is missing group_id")

    # App queries carry a JSON payload {text, history}; WhatsApp sends plain text.
    payload = str(job["payload"])
    text = payload
    history = None
    if payload.startswith("{"):
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict) and parsed.get("text"):
                text = str(parsed["text"])
                raw_history = parsed.get("history")
                if isinstance(raw_history, list):
                    history = [turn for turn in raw_history if isinstance(turn, dict)]
        except Exception:
            pass

    if str(job.get("chat_id")) == "app":
        member = conn.execute("select 1 from members where group_id = %s and wa_user_id = %s", (group_id, str(job["sender_id"]))).fetchone()
        conn.commit()
        if member is None:
            mark_job_error(conn, job["id"], "You no longer have access to this group.")
            return
    structured = answer_question_structured(group_id, text, history=history)
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


def _connected_job_loop(
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
                conn.commit()
            except Exception as exc:
                LOG.exception("Job %s failed", job.get("id"))
                conn.rollback()
                try:
                    mark_job_error(conn, job["id"], job_error_reply(exc))
                    log_event(conn, job.get("group_id"), "error", f"{type(exc).__name__}: {exc}")
                    conn.commit()
                except Exception:
                    LOG.exception("Could not record failure for job %s", job.get("id"))



def job_loop(**kwargs: Any) -> None:
    while True:
        try:
            _connected_job_loop(**kwargs)
        except Exception:
            LOG.exception("Job connection failed; reconnecting in five seconds")
            time.sleep(5)


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
