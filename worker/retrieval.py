from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import anthropic
from dotenv import load_dotenv

from db import (
    connect,
    count_filtered_items,
    count_group_items,
    group_location_stats,
    lookup_items_by_name,
    saved_items_for_summary,
    search_items,
)
from embed import embed

load_dotenv()

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
MAX_REPLY_CHARS = 520
VALID_INTENTS = {"discovery", "lookup", "meta"}
VALID_CATEGORIES = {"dining", "attraction"}

DINING_TERMS = {
    "bar",
    "brunch",
    "cafe",
    "coffee",
    "dining",
    "dinner",
    "eat",
    "food",
    "lunch",
    "restaurant",
    "restaurants",
}
ATTRACTION_TERMS = {
    "activities",
    "activity",
    "attraction",
    "attractions",
    "do",
    "see",
    "things",
    "visit",
}
KNOWN_TAGS = {
    "italian",
    "sushi",
    "handroll",
    "pizza",
    "coffee",
    "brunch",
    "vegan",
    "dessert",
    "hiking",
    "beach",
    "date night",
}


@dataclass
class QuerySlots:
    intent: str
    location: str | None = None
    category: str | None = None
    cuisine_or_tags: list[str] | None = None
    target_place: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any], text: str) -> "QuerySlots":
        fallback = deterministic_query_slots(text)
        intent = normalize_intent(raw.get("intent")) or fallback.intent
        location = normalize_location(raw.get("location")) or fallback.location
        category = normalize_category(raw.get("category")) or fallback.category

        cuisine_value = raw.get("cuisine_or_tags")
        if cuisine_value is None:
            cuisine_value = raw.get("cuisine")
        cuisine_or_tags = normalize_tags(cuisine_value)
        if not cuisine_or_tags:
            cuisine_or_tags = fallback.cuisine_or_tags or []

        target_place = clean_target_place(raw.get("target_place")) or fallback.target_place
        return cls(
            intent=intent,
            location=location,
            category=category,
            cuisine_or_tags=cuisine_or_tags,
            target_place=target_place,
        )


@dataclass
class RetrievalResult:
    question: str
    slots: QuerySlots
    items: list[dict[str, Any]]
    dominant_city: str | None = None
    defaulted_location: str | None = None
    retrieval_location: str | None = None
    empty_reason: str | None = None
    target_item: dict[str, Any] | None = None


def response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif isinstance(block, dict) and block.get("text") is not None:
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def compact_answer(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= MAX_REPLY_CHARS:
        return text

    cutoff = text.rfind(".", 0, MAX_REPLY_CHARS)
    if cutoff < 180:
        cutoff = text.rfind("\n", 0, MAX_REPLY_CHARS)
    if cutoff < 180:
        cutoff = MAX_REPLY_CHARS - 1
    return text[: cutoff + 1].rstrip() + "…"


def extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_intent(value: Any) -> str | None:
    intent = str(value or "").strip().lower()
    return intent if intent in VALID_INTENTS else None


def normalize_location(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a", "unknown"}:
        return None

    lowered = text.lower()
    lowered = lowered.replace(".", "")
    if re.search(r"(^|[^a-z])la([^a-z]|$)", lowered) or "los angeles" in lowered:
        return "Los Angeles"
    if "mumbai" in lowered or "bombay" in lowered:
        return "Mumbai"

    tokens = re.findall(r"[a-z]+", lowered)
    if any(difflib.SequenceMatcher(None, token, "mumbai").ratio() >= 0.8 for token in tokens):
        return "Mumbai"
    if len(tokens) == 1 and difflib.SequenceMatcher(None, tokens[0], "la").ratio() == 1:
        return "Los Angeles"

    return re.sub(r"\s+", " ", text).strip(" ,.'-").title() or None


def normalize_category(value: Any) -> str | None:
    if value is None:
        return None

    lowered = str(value).strip().lower()
    if not lowered or lowered in {"none", "null", "n/a", "unknown"}:
        return None
    if lowered in VALID_CATEGORIES:
        return lowered
    if any(term in lowered for term in DINING_TERMS):
        return "dining"
    if any(term in lowered for term in ATTRACTION_TERMS):
        return "attraction"
    return None


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_tags = [value]
    elif isinstance(value, list):
        raw_tags = value
    else:
        return []

    tags: list[str] = []
    for tag in raw_tags:
        clean = re.sub(r"\s+", " ", str(tag).strip().lower())
        if clean and clean not in {"none", "null"} and clean not in tags:
            tags.append(clean)
    return tags


def clean_target_place(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip().strip("\"'"))
    if not text or text.lower() in {"none", "null", "unknown"}:
        return None
    return text[:80].title()


def extract_location_from_text(text: str) -> str | None:
    normalized = normalize_location(text)
    if normalized in {"Los Angeles", "Mumbai"}:
        return normalized

    match = re.search(
        r"\b(?:in|near|around|at)\s+([A-Za-z][A-Za-z0-9 .,'-]{1,40})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    hint = re.sub(r"\b(?:today|tomorrow|tonight|this|weekend|please)\b", "", match.group(1), flags=re.I)
    hint = re.sub(r"[?!.]+$", "", hint).strip(" ,.'-")
    return normalize_location(hint[:40])


def deterministic_query_slots(text: str) -> QuerySlots:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z]+", lowered))

    intent = "discovery"
    target_place = None
    lookup_match = re.match(r"\s*([A-Za-z][A-Za-z0-9 &'/-]{1,80}?)\s+(?:is|are|was|were)\s+(?:in|near|at)\b", text)
    if lookup_match:
        intent = "lookup"
        target_place = clean_target_place(lookup_match.group(1))
    elif re.search(r"\bwhat\s+(?:else\s+)?do\s+you\s+(?:know|have)\b", lowered):
        intent = "meta"
    elif re.search(r"\b(?:what'?s|what is|show|list)\s+(?:saved|in the list|on the list)\b", lowered):
        intent = "meta"

    category = None
    if tokens & DINING_TERMS or re.search(r"\bfood recommendations\b", lowered):
        category = "dining"
    if (
        re.search(r"\bthings?\s+to\s+do\b", lowered)
        or re.search(r"\bactivities?\b", lowered)
        or re.search(r"\bplaces?\s+(?:i\s+saved\s+)?to\s+visit\b", lowered)
        or re.search(r"\bwhat\s+should\s+(?:we|i)\s+do\b", lowered)
    ):
        category = "attraction"

    tags = [tag for tag in KNOWN_TAGS if re.search(rf"\b{re.escape(tag)}\b", lowered)]
    return QuerySlots(
        intent=intent,
        location=extract_location_from_text(text),
        category=category,
        cuisine_or_tags=tags,
        target_place=target_place,
    )


def query_understanding_prompt(text: str) -> str:
    examples = [
        {
            "q": "what should we do in la?",
            "json": {
                "intent": "discovery",
                "location": "Los Angeles",
                "category": "attraction",
                "cuisine_or_tags": [],
                "target_place": None,
            },
        },
        {
            "q": "where should i eat in la",
            "json": {
                "intent": "discovery",
                "location": "Los Angeles",
                "category": "dining",
                "cuisine_or_tags": [],
                "target_place": None,
            },
        },
        {
            "q": "any good italian food ?",
            "json": {
                "intent": "discovery",
                "location": None,
                "category": "dining",
                "cuisine_or_tags": ["italian"],
                "target_place": None,
            },
        },
        {
            "q": "Which restaurant can I go to",
            "json": {
                "intent": "discovery",
                "location": None,
                "category": "dining",
                "cuisine_or_tags": [],
                "target_place": None,
            },
        },
        {
            "q": "What else do you know",
            "json": {
                "intent": "meta",
                "location": None,
                "category": None,
                "cuisine_or_tags": [],
                "target_place": None,
            },
        },
        {
            "q": "any pasta recipes?",
            "json": {
                "intent": "discovery",
                "location": None,
                "category": None,
                "cuisine_or_tags": ["pasta", "recipe"],
                "target_place": None,
            },
        },
        {
            "q": "show me the workouts i saved",
            "json": {
                "intent": "discovery",
                "location": None,
                "category": None,
                "cuisine_or_tags": ["workout"],
                "target_place": None,
            },
        },
        {
            "q": "Sugo social is in la?",
            "json": {
                "intent": "lookup",
                "location": "Los Angeles",
                "category": None,
                "cuisine_or_tags": [],
                "target_place": "Sugo Social",
            },
        },
        {
            "q": "Any good places I saved to visit in munbai?",
            "json": {
                "intent": "discovery",
                "location": "Mumbai",
                "category": "attraction",
                "cuisine_or_tags": [],
                "target_place": None,
            },
        },
    ]

    example_text = "\n".join(
        f"Q: {example['q']}\nA: {json.dumps(example['json'])}" for example in examples
    )
    return f"""
Parse this query for a bot that stores saved items from reels: places, recipes,
workouts, products, memes, and more.
Return only one JSON object with exactly these keys:
intent: discovery, lookup, or meta. Asking for a kind of saved thing (recipes, workouts, spots) is discovery; meta is only for "what do you have/know" style overview questions.
location: canonical city string or null. Normalize "la" to "Los Angeles" and "munbai"/"mumbai" to "Mumbai". Use null when no location is given.
category: dining, attraction, or null. eat/food/restaurant/dinner means dining. things to do/activities/do/see/visit means attraction. Use null for non-place queries.
cuisine_or_tags: optional finer filters like ["italian"] or ["pasta", "recipe"] or ["workout"].
target_place: place name for lookup intent, otherwise null.

Examples:
{example_text}

Q: {text}
A:
""".strip()


def parse_query_slots(text: str) -> QuerySlots:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return deterministic_query_slots(text)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=220,
            temperature=0,
            system="Return JSON only. No markdown, no prose.",
            messages=[{"role": "user", "content": query_understanding_prompt(text)}],
        )
        parsed = extract_json_object(response_text(response))
        if parsed:
            return QuerySlots.from_raw(parsed, text)
    except Exception:
        pass

    return deterministic_query_slots(text)


def city_short_name(city: str | None) -> str:
    if city == "Los Angeles":
        return "LA"
    return city or "saved places"


def item_city(item: dict[str, Any]) -> str | None:
    return normalize_location(item.get("location_text"))


def item_matches_location(item: dict[str, Any], location: str | None) -> bool:
    if not location:
        return True
    return item_city(item) == location


def dominant_city_from_stats(stats: list[dict[str, Any]]) -> str | None:
    scores: dict[str, tuple[int, int]] = {}
    for row in stats:
        city = normalize_location(row.get("location_text"))
        if not city:
            continue
        save_count = int(row.get("save_count") or 0)
        item_count = int(row.get("item_count") or 0)
        score, items = scores.get(city, (0, 0))
        scores[city] = (score + max(save_count, item_count), items + item_count)

    if not scores:
        return None
    return max(scores.items(), key=lambda item: (item[1][0], item[1][1], item[0]))[0]


def dominant_city(conn: Any, group_id: str) -> str | None:
    return dominant_city_from_stats(group_location_stats(conn, group_id))


def retrieve_for_query(group_id: str, text: str) -> RetrievalResult:
    slots = parse_query_slots(text)

    with connect() as conn:
        if count_group_items(conn, group_id) == 0:
            return RetrievalResult(text, slots, [], empty_reason="no_group_items")

        top_city = dominant_city(conn, group_id)

        if slots.intent == "meta":
            return RetrievalResult(
                text,
                slots,
                saved_items_for_summary(conn, group_id, limit=40),
                dominant_city=top_city,
            )

        if slots.intent == "lookup":
            lookup_items = lookup_items_by_name(
                conn,
                group_id=group_id,
                target_place=slots.target_place or text,
                limit=5,
            )
            preferred = next((item for item in lookup_items if item_matches_location(item, slots.location)), None)
            target_item = preferred or (lookup_items[0] if lookup_items else None)
            return RetrievalResult(
                text,
                slots,
                [target_item] if target_item else [],
                dominant_city=top_city,
                retrieval_location=slots.location,
                empty_reason=None if target_item else "lookup_empty",
                target_item=target_item,
            )

        retrieval_location = slots.location
        defaulted_location = None
        if retrieval_location is None and top_city is not None:
            retrieval_location = top_city
            defaulted_location = top_city

        if retrieval_location:
            location_count = count_filtered_items(
                conn,
                group_id=group_id,
                location=retrieval_location,
            )
            if location_count == 0:
                if defaulted_location is not None:
                    retrieval_location = None
                    defaulted_location = None
                else:
                    return RetrievalResult(
                        text,
                        slots,
                        [],
                        dominant_city=top_city,
                        defaulted_location=defaulted_location,
                        retrieval_location=retrieval_location,
                        empty_reason="location_empty",
                    )

        question_vector = embed(text)
        items = search_items(
            conn,
            group_id=group_id,
            embedding=question_vector,
            limit=8,
            location=retrieval_location,
            category=slots.category,
            cuisine_or_tags=slots.cuisine_or_tags or [],
        )
        if not items and defaulted_location is not None:
            items = search_items(
                conn,
                group_id=group_id,
                embedding=question_vector,
                limit=8,
                location=None,
                category=slots.category,
                cuisine_or_tags=slots.cuisine_or_tags or [],
            )
            if items:
                defaulted_location = None
                retrieval_location = None
        return RetrievalResult(
            text,
            slots,
            items,
            dominant_city=top_city,
            defaulted_location=defaulted_location,
            retrieval_location=retrieval_location,
            empty_reason=None if items else "filter_empty",
        )


def social_proof(item: dict[str, Any]) -> str:
    save_count = int(item.get("save_count") or 0)
    if save_count > 1:
        return f" ({save_count} of you saved this)"
    return ""


def place_label(item: dict[str, Any]) -> str:
    name = str(item.get("place_name") or "").strip()
    return name or "this saved spot"


def join_natural(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def grounded_empty_reply(location: str | None, dominant: str | None) -> str:
    return f"We don't have any saved places in {location} yet — the list is mostly {dominant or 'another city'}."


def compose_discovery_answer(result: RetrievalResult) -> str:
    if result.empty_reason == "location_empty":
        return grounded_empty_reply(result.retrieval_location, result.dominant_city)

    if result.empty_reason == "filter_empty":
        bits: list[str] = []
        if result.slots.cuisine_or_tags:
            bits.append(", ".join(result.slots.cuisine_or_tags))
        if result.slots.category:
            bits.append(result.slots.category)
        descriptor = " ".join(bits) or "matching"
        location = result.retrieval_location
        if location:
            return f"I don't see anything saved for {descriptor} in {city_short_name(location)} yet."
        return f"I don't see anything saved for {descriptor} yet."

    picks = result.items[:4]
    if not picks:
        return "I don't have enough saved places to answer that yet."

    labels = [f"{place_label(item)}{social_proof(item)}" for item in picks]
    if result.slots.category == "dining":
        body = f"For food, I'd pick {join_natural(labels)}."
    elif result.slots.category == "attraction":
        body = f"I'd start with {join_natural(labels)}."
    else:
        body = f"I'd pick {join_natural(labels)}."

    if result.defaulted_location:
        prefix = f"Assuming {city_short_name(result.defaulted_location)} since that's most of what you've saved — "
        body = prefix + body[0].lower() + body[1:]
    return body


def compose_lookup_answer(result: RetrievalResult) -> str:
    item = result.target_item
    target = result.slots.target_place or "that place"
    if not item:
        return f"I don't have {target} saved for this group."

    place = place_label(item)
    location_text = str(item.get("location_text") or "").strip()
    if result.slots.location and location_text:
        if item_matches_location(item, result.slots.location):
            return f"Yes — {place} is in {location_text}."
        return f"I have {place} saved in {location_text}, not {city_short_name(result.slots.location)}."
    if location_text:
        return f"{place} is saved in {location_text}."

    category = str(item.get("category") or "saved place").strip()
    return f"{place} is saved as {category}, but I don't have a location field for it."


def compose_meta_answer(result: RetrievalResult) -> str:
    if not result.items:
        return "No saved places yet for this group."

    grouped: dict[str, dict[str, list[str]]] = {}
    for item in result.items:
        city = city_short_name(item_city(item) or str(item.get("location_text") or "General"))
        list_name = str(item.get("list_name") or item.get("category") or "Saved places").strip()
        grouped.setdefault(city, {}).setdefault(list_name, [])
        if len(grouped[city][list_name]) < 3:
            grouped[city][list_name].append(place_label(item))

    city_chunks: list[str] = []
    for city, lists in list(grouped.items())[:4]:
        list_chunks = []
        for list_name, places in list(lists.items())[:3]:
            list_chunks.append(f"{list_name}: {join_natural(places)}")
        city_chunks.append(f"{city} — {'; '.join(list_chunks)}")

    return "Saved so far: " + " | ".join(city_chunks) + "."


def compose_answer(result: RetrievalResult) -> str:
    if result.empty_reason == "no_group_items":
        return "No saved places yet for this group."

    if result.slots.intent == "meta":
        return compose_meta_answer(result)
    if result.slots.intent == "lookup":
        return compose_lookup_answer(result)
    return compose_discovery_answer(result)


def answer_question(group_id: str, text: str) -> str:
    return compact_answer(compose_answer(retrieve_for_query(group_id, text)))
