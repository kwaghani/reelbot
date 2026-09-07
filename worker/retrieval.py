from __future__ import annotations

import difflib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any

import anthropic
from dotenv import load_dotenv

from content_quality import answerable_content

from db import (
    connect,
    count_filtered_items,
    count_group_items,
    group_location_stats,
    lookup_items_by_name,
    saved_items_for_summary,
    search_items,
)
from embed import embed_query

load_dotenv()

QUALITY_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-fable-5").strip() or "claude-fable-5"
FAST_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"
MAX_REPLY_CHARS = 900
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

TAG_ALIASES = {
    "funny": ["humor", "funny", "comedy", "meme"],
    "joke": ["humor", "funny", "comedy", "joke"],
    "meme": ["humor", "funny", "comedy", "meme"],
    "pickup line": ["pickup lines", "flirting", "dating", "relationships"],
    "pick up line": ["pickup lines", "flirting", "dating", "relationships"],
    "relationship": ["relationships", "dating", "couples"],
    "couple": ["couples", "relationships", "dating"],
    "football": ["football", "soccer", "fifa"],
    "soccer": ["football", "soccer", "fifa"],
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
        if fallback.intent == "lookup" and fallback.target_place:
            intent = "lookup"
        if intent == "meta" and not is_library_overview_query(text):
            # "Do you have a pickup line?" asks for content; it is not an
            # overview of the entire library.
            intent = fallback.intent if fallback.intent != "meta" else "discovery"
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
    broad_plan: bool = False


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


def polish_answer_presentation(text: str) -> str:
    """Keep chat copy readable even when a model returns casual lowercase text."""
    substitutions = [
        # Keep title-cased names such as "La Brea" intact while normalizing
        # the common lowercase city abbreviation.
        (r"\bla\b", "LA", 0),
        (r"\bnyc\b", "NYC", re.IGNORECASE),
        (r"\bsf\b", "SF", re.IGNORECASE),
        (r"\blos angeles\b", "Los Angeles", re.IGNORECASE),
        (r"\bnew york city\b", "New York City", re.IGNORECASE),
        (r"\bnew york\b", "New York", re.IGNORECASE),
        (r"\bpoint dume\b", "Point Dume", re.IGNORECASE),
        (r"\bhollywood sign\b", "Hollywood Sign", re.IGNORECASE),
        (r"\bmalibu\b", "Malibu", re.IGNORECASE),
        (r"\bcalifornia\b", "California", re.IGNORECASE),
        (r"\bmanhattan\b", "Manhattan", re.IGNORECASE),
        (r"\bbrooklyn\b", "Brooklyn", re.IGNORECASE),
        (r"\bmumbai\b", "Mumbai", re.IGNORECASE),
        (r"\bmunich\b", "Munich", re.IGNORECASE),
    ]
    polished = text.strip()
    for pattern, replacement, flags in substitutions:
        polished = re.sub(pattern, replacement, polished, flags=flags)

    lines: list[str] = []
    for line in polished.splitlines():
        clean = line.rstrip()
        clean = re.sub(r"^\s*-\s+", "• ", clean)
        first_letter = re.search(r"[A-Za-z]", clean)
        if first_letter:
            index = first_letter.start()
            clean = clean[:index] + clean[index].upper() + clean[index + 1 :]
        clean = re.sub(
            r"([.!?][\"'”’)]*\s+)([a-z])",
            lambda match: match.group(1) + match.group(2).upper(),
            clean,
        )
        lines.append(clean)
    return "\n".join(lines)


def answer_payload(answer: str, sources: list[dict[str, str]]) -> dict[str, Any]:
    return {"answer": polish_answer_presentation(answer), "sources": sources}


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


def is_library_overview_query(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", text.strip().lower())
    patterns = [
        r"\bwhat (?:else )?do you (?:know|have)(?: saved)?\b\s*[?.!]*$",
        r"\bwhat(?:'s| is) (?:in|inside) (?:my|our|the) (?:saved |reel )?(?:library|folders?|collection|list)\b\s*[?.!]*$",
        r"\bwhat do (?:i|we) have saved\b\s*[?.!]*$",
        r"\bshow (?:me|us) (?:everything|all) (?:i|we|you)(?:'ve| have)? saved\b\s*[?.!]*$",
        r"\blist (?:my|our|the) (?:saved )?(?:items|reels|folders|collection)\b\s*[?.!]*$",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def explicitly_uses_saved_content(text: str) -> bool:
    """Catch requests that must use the library even if model routing drifts."""
    lowered = re.sub(r"\s+", " ", text.strip().lower())
    patterns = [
        r"\b(?:saved|saves|folders?|library)\b",
        r"\b(?:my|our|the group's|the group) reels?\b",
        r"\bbased on (?:what|anything|everything|things?|stuff).{0,35}\b(?:sent|shared)\b",
        r"\b(?:what|anything|everything|things?|stuff) (?:i|we)(?:'ve| have)? (?:sent|shared) (?:you|with you)\b",
        r"\b(?:i|we)(?:'ve| have)? (?:sent|shared) (?:you|with you) (?:anything|everything|things?|stuff)\b",
        r"\b(?:use|using|reference|referencing|look at|looking at).{0,30}\b(?:what i sent|what we sent|them|those)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def is_broad_plan_query(text: str) -> bool:
    """A plan can span food, outdoors, and activities; it is not one category."""
    lowered = re.sub(r"\s+", " ", text.strip().lower())
    broad_patterns = [
        r"\bthings? to do\b",
        r"\bwhat should (?:i|we) do\b",
        r"\b(?:plan|build|make|put together) (?:me |us |my |our |a )?(?:weekend|day|trip|date|itinerary)\b",
        r"\b(?:weekend|day trip|day out|itinerary)\b",
    ]
    if not any(re.search(pattern, lowered) for pattern in broad_patterns):
        return False
    # "Dinner this weekend" and similar narrow requests should keep their
    # explicit dining filter. Generic "things to do" remains cross-category.
    has_generic_plan_language = bool(
        re.search(r"\bthings? to do\b|\bwhat should (?:i|we) do\b|\b(?:plan|itinerary)\b", lowered)
    )
    has_dining_request = bool(set(re.findall(r"[a-z]+", lowered)) & DINING_TERMS)
    return has_generic_plan_language or not has_dining_request


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
    if re.search(r"\b(save|saved|reel|reels|library|folder|folders|list|group|collection)\b", hint, flags=re.I):
        return None
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
    elif is_library_overview_query(text):
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
    for phrase, aliases in TAG_ALIASES.items():
        if phrase in lowered:
            tags.extend(alias for alias in aliases if alias not in tags)
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
            "q": "Do you have any pickup lines for me?",
            "json": {
                "intent": "discovery",
                "location": None,
                "category": None,
                "cuisine_or_tags": ["pickup lines", "flirting", "dating"],
                "target_place": None,
            },
        },
        {
            "q": "show me something funny",
            "json": {
                "intent": "discovery",
                "location": None,
                "category": None,
                "cuisine_or_tags": ["humor", "comedy", "meme"],
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
intent: discovery, lookup, or meta. Asking for a kind of thing, an answer, or
examples (recipes, workouts, pickup lines, jokes, captions, spots) is discovery.
meta is ONLY a whole-library overview such as "what do I have saved?" or the
exact broad follow-up "what else do you know?". "Do you have any X?" is
discovery whenever X is named.
location: canonical city string or null. Normalize "la" to "Los Angeles" and "munbai"/"mumbai" to "Mumbai". Use null when no location is given.
category: dining, attraction, or null. eat/food/restaurant/dinner means dining. things to do/activities/do/see/visit means attraction. Use null for non-place queries.
cuisine_or_tags: useful search concepts and synonyms, not just cuisine. For
example ["italian"], ["pasta", "recipe"], ["workout"], ["pickup lines",
"flirting", "dating"], or ["humor", "comedy", "meme"].
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
        client = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        response = client.messages.create(
            model=FAST_MODEL,
            max_tokens=220,
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
        raw_location = str(row.get("location_text") or "")
        if re.search(r"\d", raw_location):
            # Street addresses ("727 Manhattan Ave, ...") are not cities.
            continue
        city = normalize_location(raw_location)
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


def should_default_location(slots: QuerySlots) -> bool:
    """Only place searches should inherit the group's dominant city."""
    return slots.category in VALID_CATEGORIES


SEARCH_STOPWORDS = {
    "a", "about", "any", "are", "can", "could", "do", "for", "from", "give",
    "good", "have", "i", "in", "is", "me", "my", "of", "on", "please", "saved",
    "show", "some", "that", "the", "to", "we", "what", "where", "which", "with",
    "you", "your",
}


def search_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 2 or token in SEARCH_STOPWORDS:
            continue
        tokens.add(token)
        if token.endswith("ies") and len(token) > 4:
            tokens.add(token[:-3] + "y")
        elif token.endswith("s") and len(token) > 3:
            tokens.add(token[:-1])
    return tokens


def item_search_text(item: dict[str, Any]) -> str:
    values = [
        item.get("place_name"),
        item.get("category"),
        item.get("list_name"),
        item.get("location_text"),
        " ".join(str(tag) for tag in item.get("tags") or []),
        answerable_content(item.get("transcript"))[:1200],
    ]
    return re.sub(r"\s+", " ", " ".join(str(value) for value in values if value)).lower()


def hybrid_rank_items(question: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rerank vector candidates with lexical evidence and a tiny social tie-breaker."""
    query_tokens = search_tokens(question)
    normalized_query = re.sub(r"\s+", " ", question.strip().lower())
    ranked: list[dict[str, Any]] = []
    for original in items:
        item = dict(original)
        blob = item_search_text(item)
        item_tokens = search_tokens(blob)
        overlap = len(query_tokens & item_tokens) / max(len(query_tokens), 1)
        distance = float(item.get("distance") if item.get("distance") is not None else 1.0)
        semantic = max(0.0, min(1.0, 1.0 - distance))
        phrase = 1.0 if len(normalized_query) >= 4 and normalized_query in blob else 0.0
        saves = max(0, int(item.get("save_count") or 0))
        social = min(1.0, math.log2(saves + 1) / 4.0)
        score = (0.70 * semantic) + (0.24 * overlap) + (0.04 * phrase) + (0.02 * social)
        item["semantic_score"] = semantic
        item["lexical_score"] = overlap
        item["relevance_score"] = score
        ranked.append(item)
    return sorted(
        ranked,
        key=lambda item: (
            float(item.get("relevance_score") or 0.0),
            int(item.get("save_count") or 0),
        ),
        reverse=True,
    )


def keep_relevant_items(items: list[dict[str, Any]], *, filtered: bool) -> list[dict[str, Any]]:
    if not items:
        return []
    kept = [
        item
        for item in items
        if float(item.get("lexical_score") or 0.0) >= 0.12
        or float(item.get("semantic_score") or 0.0) >= (0.50 if filtered else 0.47)
    ]
    if not kept:
        return []
    best = float(kept[0].get("relevance_score") or 0.0)
    return [item for item in kept if float(item.get("relevance_score") or 0.0) >= best - 0.18][:8]


def rank_search_results(
    question: str,
    items: list[dict[str, Any]],
    *,
    filtered: bool,
) -> list[dict[str, Any]]:
    return keep_relevant_items(hybrid_rank_items(question, items), filtered=filtered)


def retrieve_for_query(
    group_id: str,
    text: str,
    slots: QuerySlots | None = None,
) -> RetrievalResult:
    slots = slots or parse_query_slots(text)
    broad_plan = is_broad_plan_query(text)

    with connect() as conn:
        if count_group_items(conn, group_id) == 0:
            return RetrievalResult(
                text, slots, [], empty_reason="no_group_items", broad_plan=broad_plan
            )

        top_city = dominant_city(conn, group_id)

        if slots.intent == "meta":
            return RetrievalResult(
                text,
                slots,
                saved_items_for_summary(conn, group_id, limit=40),
                dominant_city=top_city,
                broad_plan=broad_plan,
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
            if target_item:
                return RetrievalResult(
                    text,
                    slots,
                    [target_item],
                    dominant_city=top_city,
                    retrieval_location=slots.location,
                    target_item=target_item,
                    broad_plan=broad_plan,
                )
            # A confident named lookup must not degrade into unrelated semantic
            # recommendations. Say the item is not saved in this group.
            return RetrievalResult(
                text,
                slots,
                [],
                dominant_city=top_city,
                retrieval_location=slots.location,
                empty_reason="lookup_empty",
                broad_plan=broad_plan,
            )

        retrieval_location = slots.location
        defaulted_location = None
        if retrieval_location is None and top_city is not None and should_default_location(slots):
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
                        broad_plan=broad_plan,
                    )

        question_vector = embed_query(text)
        # A weekend/day/trip plan should consider every matching saved category.
        # Keeping `category=attraction` here used to hide restaurants before the
        # answer model ever saw them.
        search_category = None if broad_plan else slots.category
        search_tags = slots.cuisine_or_tags or []
        has_topic_filters = bool(search_category or search_tags)
        items = search_items(
            conn,
            group_id=group_id,
            embedding=question_vector,
            limit=16,
            location=retrieval_location,
            category=search_category,
            cuisine_or_tags=search_tags,
        )
        if broad_plan:
            # Location and any explicit interests already bound the candidate
            # set. Keep the cross-category candidates instead of discarding a
            # cafe just because its embedding is less similar to "things to do".
            items = hybrid_rank_items(text, items)[:8]
        else:
            items = rank_search_results(
                text, items, filtered=bool(retrieval_location or has_topic_filters)
            )
        if not items and defaulted_location is not None:
            items = search_items(
                conn,
                group_id=group_id,
                embedding=question_vector,
                limit=16,
                location=None,
                category=search_category,
                cuisine_or_tags=search_tags,
            )
            if broad_plan:
                items = hybrid_rank_items(text, items)[:8]
            else:
                items = rank_search_results(text, items, filtered=has_topic_filters)
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
            broad_plan=broad_plan,
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
        return "I don't have enough relevant saved items to answer that yet."

    labels = [f"{place_label(item)}{social_proof(item)}" for item in picks]
    if result.broad_plan:
        body = f"Based on your saves, I'd start with {join_natural(labels)}."
    elif result.slots.category == "dining":
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
        return "Nothing saved yet for this group."

    grouped: dict[str, list[str]] = {}
    for item in result.items:
        folder = str(item.get("list_name") or item.get("category") or "Other").strip()
        grouped.setdefault(folder, [])
        if len(grouped[folder]) < 3:
            label = place_label(item)
            location = item.get("location_text")
            if location and folder in {"Restaurants", "Cafes & Desserts", "Bars & Nightlife", "Travel", "Things To Do", "Outdoors"}:
                label += f" ({city_short_name(item_city(item) or str(location))})"
            grouped[folder].append(label)

    chunks = [f"{folder} — {join_natural(items)}" for folder, items in list(grouped.items())[:8]]
    return "Saved so far: " + " | ".join(chunks) + "."


def compose_answer(result: RetrievalResult) -> str:
    if result.empty_reason == "no_group_items":
        return "Nothing saved yet! Share a reel and I'll start filing things away."

    if result.slots.intent == "meta":
        return compose_meta_answer(result)
    if result.slots.intent == "lookup":
        return compose_lookup_answer(result)
    return compose_discovery_answer(result)


MAX_LLM_ANSWER_CHARS = 1200
MAX_SOURCES = 4

PERSONA = (
    "You are ReelBot, the group's friend who remembers every reel they've saved. "
    "You text like a real person: casual, warm, brief, contractions, no corporate "
    "phrasing. Always use normal sentence capitalization and preserve the correct "
    "capitalization of cities, venues, people, and other proper nouns. Never write "
    "an all-lowercase answer. Never mention being an AI, assistant, bot, or model. "
    "Never promise future actions (no 'I'll keep an eye out'). Plain text, no markdown."
)

GREETING_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|yo|sup|what'?s up|good (morning|afternoon|evening)|"
    r"thanks?( you| u| man| bro)?|ty|thx|ok(ay)?|cool|nice|lol|haha+)\s*[!.?]*\s*$",
    re.IGNORECASE,
)


def history_block(history: list[dict[str, Any]] | None) -> str:
    if not history:
        return ""
    lines: list[str] = []
    for turn in history[-10:]:
        role = "Them" if str(turn.get("role")) == "user" else "You"
        text = re.sub(r"\s+", " ", str(turn.get("text") or "")).strip()[:400]
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def folder_overview(group_id: str) -> tuple[list[str], list[str]]:
    """Compact library catalog so routing sees topics, not only titles."""
    with connect() as conn:
        rows = conn.execute(
            """
            select coalesce(list_name, 'Other') as folder, count(*) as count
              from items
             where group_id = %s
             group by 1
             order by count(*) desc
             limit 12
            """,
            (group_id,),
        ).fetchall()
        names = conn.execute(
            """
            select place_name, list_name, subfolder, category,
                   coalesce(tags, array[]::text[]) as tags
              from items
             where group_id = %s and nullif(btrim(place_name), '') is not null
             order by created_at desc
             limit 40
            """,
            (group_id,),
        ).fetchall()
    folders = [f"{row['folder']} ({row['count']})" for row in rows]
    catalog = []
    for row in names:
        folder = str(row.get("list_name") or "Other")
        if row.get("subfolder"):
            folder += f" > {row['subfolder']}"
        details = [folder]
        if row.get("category"):
            details.append(str(row["category"]))
        tags = [str(tag) for tag in row.get("tags") or []][:5]
        if tags:
            details.append(", ".join(tags))
        catalog.append(f"{row['place_name']} [{'; '.join(details)}]")
    return folders, catalog


def route_message(
    text: str,
    history: list[dict[str, Any]] | None,
    folders: list[str],
    item_names: list[str] | None = None,
) -> dict[str, Any] | None:
    """Choose social, saved-library, or general knowledge behavior."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    prompt = f"""
{PERSONA}

Their saved folders: {", ".join(folders) or "nothing saved yet"}
Their saved items include: {", ".join(item_names or []) or "none"}
If the message mentions any of those items (even loosely), it is about their
saved library.

Conversation so far:
{history_block(history) or "(new conversation)"}

They just sent: {text}

Return ONLY JSON:
{{"mode": "social"|"library"|"general", "reply": str|null,
  "question": str|null, "slots": object|null}}

Use "library" when the request likely has a useful match in their saved folders
or items, explicitly mentions saves/reels/folders, or follows up on an answer
that used saved content. This includes requests that omit the word "saved" when
the library clearly has the topic: "give me a chest workout" with a Workouts
folder, "show me something funny" with Comedy & Memes, or "best pizza?" with
Restaurants. Follow-ups such as "does it need equipment?" stay library. Set
question to a self-contained topic query that resolves pronouns from history.
Never include words like saved, reels, folders, or library in the rewritten
question. Leave reply null.
For library mode, also fill slots with exactly these keys so retrieval does not
need another model call: intent (discovery|lookup|meta), location (city or null),
category (dining|attraction|null), cuisine_or_tags (specific search concepts),
and target_place (named saved item for lookup or null). "Do you have X saved?"
is discovery, not meta. Meta is only a whole-library overview.

Use "general" for a real question or creative/help request that does not depend
on their saved content: factual explanations, writing help, brainstorming, or
"Do you have any pickup lines for me?" when no pickup-line item/folder exists.
Do NOT confuse "do you have X?" with a library overview unless they explicitly
say saved/reels/library. Keep question as a self-contained version of the request
and leave reply null.
Set slots null.

Use "social" ONLY for greetings, thanks, acknowledgements, or casual chit-chat
with no request for information, advice, or generated content. Write reply as
1-2 short sentences and leave question and slots null.

Examples:
- "Do you have any pickup lines for me?" -> general
- "Do I have any pickup-line reels saved?" -> library
- "Give me a chest workout" with a Workouts folder -> library
- "What is the capital of France?" -> general
- "Help me write a birthday caption" -> general
- "Does it need equipment?" after discussing a saved workout -> library
- "thanks bro" -> social
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        response = client.messages.create(
            model=FAST_MODEL,
            max_tokens=350,
            system="Return JSON only. No markdown, no prose.",
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = extract_json_object(response_text(response))
        if parsed and parsed.get("mode") in {"social", "library", "general"}:
            return parsed
    except Exception:
        pass
    return None


def compose_general_answer(
    text: str,
    history: list[dict[str, Any]] | None = None,
) -> str | None:
    """Answer useful non-library questions without pretending saves support it."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    prompt = f"""
{PERSONA}

Conversation so far:
{history_block(history) or "(new conversation)"}

They just asked: {text}

Answer the request directly and use your general knowledge. This answer is not
grounded in their saved reels, so never claim that it came from their saves and
never invent a reel or source. Be accurate and practical. If the question is
ambiguous, make the smallest reasonable assumption and mention it briefly. If
you are genuinely unsure or the answer depends on live/current information you
do not have, say so plainly. For medical, legal, or financial topics, be careful
about uncertainty and avoid overconfident personalized directives.

For creative requests such as pickup lines, captions, replies, or ideas, give
several genuinely usable options in distinct tones. For factual questions, give
the answer first, then only the context needed. Keep it conversational and under
180 words. Plain text, no URLs, and no fake citations.
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        response = client.messages.create(
            model=QUALITY_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response_text(response).strip()
    except Exception:
        return None
    return answer[:MAX_LLM_ANSWER_CHARS] if answer else None


def item_context_block(index: int, item: dict[str, Any]) -> str:
    lines = [f"[{index}] {place_label(item)}"]
    for label, key in [
        ("folder", "list_name"),
        ("category", "category"),
        ("location", "location_text"),
        ("price", "price_tier"),
    ]:
        value = str(item.get(key) or "").strip()
        if value:
            lines.append(f"  {label}: {value}")
    tags = item.get("tags") or []
    if tags:
        lines.append(f"  tags: {', '.join(str(tag) for tag in tags[:8])}")
    content = re.sub(r"\s+", " ", answerable_content(item.get("transcript"))).strip()
    if content:
        lines.append(f"  content: {content[:800]}")
    save_count = int(item.get("save_count") or 0)
    if save_count > 1:
        lines.append(f"  saved by {save_count} people")
    return "\n".join(lines)


def verify_grounded_answer(
    *,
    question: str,
    draft: str,
    context: str,
    source_count: int,
) -> dict[str, Any] | None:
    """Remove unsupported claims and produce an auditable source selection."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    prompt = f"""
Audit this draft answer against the supplied saved-reel evidence.
Return ONLY JSON: {{"answer": str, "sources": [int], "supported": bool}}

Question: {question}

Draft answer:
{draft}

Evidence:
{context}

Rules:
- Keep only claims directly stated or unambiguously supported by the evidence.
- Remove plausible-sounding general knowledge, assumptions, extrapolations,
  recommendations, quantities, timings, prices, or details that are not there.
- Do not add new factual claims while correcting the draft.
- Preserve a useful, natural answer when evidence supports one.
- Preserve useful section breaks and bullet lines from the draft.
- Correct sentence capitalization and proper-noun capitalization. Never return
  an all-lowercase answer.
- sources contains only evidence numbers actually used, from 1 to {source_count}.
- Evidence is present here. Never claim that the group has no saves, no reels,
  or nothing to work with when any supplied item helps answer the question.
- Set supported=false only when no useful part of the question can be answered
  from this evidence; then explain the specific missing information briefly.
- Plain text answer, under 130 words, no URLs or citation markers.
""".strip()
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        response = client.messages.create(
            model=FAST_MODEL,
            max_tokens=500,
            system="Return JSON only. No markdown, no prose.",
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = extract_json_object(response_text(response))
    except Exception:
        return None
    if not parsed:
        return None
    answer = str(parsed.get("answer") or "").strip()
    raw_sources = parsed.get("sources")
    sources = []
    if isinstance(raw_sources, list):
        for value in raw_sources:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= index <= source_count and index not in sources:
                sources.append(index)
    if not answer:
        return None
    return {
        "answer": answer[:MAX_LLM_ANSWER_CHARS],
        "used": sources,
        "supported": bool(parsed.get("supported", True)),
    }


EMPTY_LIBRARY_CLAIM_RE = re.compile(
    r"\b(?:"
    r"(?:do not|don't|dont|did not|didn't|cannot|can't|cant) (?:actually )?(?:have|see|find) (?:any|anything)|"
    r"(?:not|aren't|isn't) (?:actually )?(?:seeing|finding) (?:any|anything)|"
    r"nothing (?:is )?saved|nothing (?:here|to work with)|"
    r"no saved (?:items?|reels?|places?)|working with nothing"
    r")\b",
    re.IGNORECASE,
)


def answer_denies_available_evidence(answer: str) -> bool:
    """Reject the exact contradiction shown in the app screenshots."""
    normalized = re.sub(r"\s+", " ", answer).strip()
    return bool(EMPTY_LIBRARY_CLAIM_RE.search(normalized))


def compose_llm_answer(
    result: RetrievalResult,
    history: list[dict[str, Any]] | None = None,
    user_message: str | None = None,
) -> dict[str, Any] | None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not result.items:
        return None

    picks = result.items[:MAX_SOURCES]
    context = "\n".join(item_context_block(i + 1, item) for i, item in enumerate(picks))
    conversation = history_block(history)
    prompt = f"""
{PERSONA}

Below are the group's saved items most relevant to what they're asking, with
everything known about them.

{"Conversation so far:" + chr(10) + conversation + chr(10) if conversation else ""}
They just asked: {user_message or result.question}
(what to look up: {result.question})

Reply the way a knowledgeable friend texts back:
- Lead with the substance immediately ("A good chest workout from your saves:
  incline press, ..."), never meta-talk like "I found a saved item titled...".
- Pull concrete details out of the content field: exercises, ingredients,
  steps, dishes, prices, neighborhoods, vibes.
- Reel content can contain background-song lyrics, hashtags, sponsor copy, OCR
  mistakes, and unrelated audio. Ignore those unless they are clearly part of
  the reel's subject. Never turn a lyric fragment into an instruction or fact.
- For broad asks (a trip, a day out, a holiday), organize across items:
  food spots together, activities together, one short line each.
- For city or recommendation requests, never return one large paragraph. Start
  with one short sentence, then use 2-4 useful category headings such as
  "Outdoors", "Food", "Things to Do", or "Nightlife". Under each heading,
  format each recommendation on its own line as "• Name — concise reason".
  Omit empty categories and do not create categories unsupported by the saves.
- For all other multi-item answers, use short lines or a compact bulleted list.
- Use normal sentence capitalization. Capitalize proper nouns exactly as they
  appear in the saved evidence, including LA, venue names, and neighborhoods.
  Never write the answer in an all-lowercase texting style.
- Only use facts from the items; never invent details. If an item's content
  is thin, give what is known in one clause and move on — no apologizing.
- Treat missing information as missing. Never fill gaps with standard advice
  or likely details from general knowledge. Do not add sets, reps, frequency,
  ingredients, prices, exercise names, or place facts unless they are explicitly
  present. For example, "targets lower chest" does not justify inventing a
  decline exercise. Silently verify every concrete claim against the supplied
  item before writing it.
- If this is a follow-up, answer just the follow-up; don't repeat everything.
- Under 130 words, plain text, no URLs, no bracketed citations (tappable
  sources are shown separately below your reply).
- After your reply, add one final line exactly like: SOURCES: 1,3
  listing the numbers of the items your reply actually used. This line is
  stripped before the user sees anything.

Saved items:
{context}
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        response = client.messages.create(
            model=QUALITY_MODEL,
            max_tokens=450,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response_text(response).strip()
    except Exception:
        return None

    if not answer:
        return None

    used: list[int] | None = None
    match = re.search(r"\n?\s*SOURCES:\s*([0-9,\s]*)\s*$", answer)
    if match:
        answer = answer[: match.start()].strip()
        used = [int(part) for part in re.findall(r"\d+", match.group(1))]
    if not answer:
        return None
    verified = verify_grounded_answer(
        question=user_message or result.question,
        draft=answer,
        context=context,
        source_count=len(picks),
    )
    if verified:
        verified_answer = str(verified["answer"])
        if (
            not verified.get("supported")
            or not verified.get("used")
            or answer_denies_available_evidence(verified_answer)
        ):
            return None
        verified["answer"] = polish_answer_presentation(verified_answer)
        return verified
    # If evidence verification fails, use the deterministic source-only answer.
    return None


def answer_sources(result: RetrievalResult, used: list[int] | None = None) -> list[dict[str, str]]:
    picks = result.items[:MAX_SOURCES]
    if used is not None:
        chosen = [picks[index - 1] for index in used if 1 <= index <= len(picks)]
        picks = chosen
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in picks:
        url = str(item.get("source_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({"title": place_label(item), "url": url})
    return sources


def answer_question_structured(
    group_id: str,
    text: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if GREETING_RE.match(text):
        return answer_payload("Hey! Ask me about your saved reels or anything else you need.", [])

    folders, item_names = folder_overview(group_id)
    routed = route_message(text, history, folders, item_names)
    force_library = explicitly_uses_saved_content(text)

    if (
        routed
        and not force_library
        and routed.get("mode") == "social"
        and str(routed.get("reply") or "").strip()
    ):
        return answer_payload(compact_answer(str(routed["reply"]).strip()), [])

    routed_mode = str((routed or {}).get("mode") or "")
    question = (
        str((routed or {}).get("question") or "").strip()
        if routed_mode == "library" or not force_library
        else ""
    ) or text
    if is_library_overview_query(text):
        # Preserve overview wording so query parsing returns meta instead of
        # semantically ranking only a few arbitrary items.
        question = text
    if routed and not force_library and routed.get("mode") == "general":
        answer = compose_general_answer(question, history=history)
        if answer:
            return answer_payload(compact_answer(answer), [])
        return answer_payload(
            "I couldn't get a reliable answer to that just now. Try asking it one more way.", []
        )

    routed_slots = None
    if routed and routed.get("mode") == "library" and isinstance(routed.get("slots"), dict):
        routed_slots = QuerySlots.from_raw(routed["slots"], question)
    result = retrieve_for_query(group_id, question, slots=routed_slots)

    if result.empty_reason or not result.items:
        return answer_payload(compose_answer(result), [])

    if result.slots.intent == "meta":
        return answer_payload(compose_meta_answer(result), [])

    composed = compose_llm_answer(result, history=history, user_message=text)
    if composed:
        return answer_payload(
            str(composed["answer"]), answer_sources(result, composed.get("used"))
        )
    return answer_payload(compact_answer(compose_answer(result)), answer_sources(result))


def plain_answer_with_sources(structured: dict[str, Any]) -> str:
    answer = str(structured.get("answer") or "").strip()
    sources = structured.get("sources") or []
    if not sources:
        return answer
    lines = [f"• {source['title']}: {source['url']}" for source in sources]
    return answer + "\n\nSources:\n" + "\n".join(lines)


def answer_question(group_id: str, text: str) -> str:
    return plain_answer_with_sources(answer_question_structured(group_id, text))
