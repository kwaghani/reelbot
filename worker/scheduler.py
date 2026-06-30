from __future__ import annotations

import argparse
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from db import (
    connect,
    enqueue_nudge,
    has_recent_nudge,
    list_groups,
    recent_nudge_cluster_keys,
    saved_items_for_nudges,
)

ROOT = Path(__file__).resolve().parents[1]
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

DINING_TERMS = {
    "bakery",
    "bar",
    "brunch",
    "cafe",
    "coffee",
    "dessert",
    "dining",
    "dinner",
    "eat",
    "food",
    "lunch",
    "restaurant",
    "restaurants",
    "sushi",
}
ATTRACTION_TERMS = {
    "activities",
    "activity",
    "attraction",
    "attractions",
    "beach",
    "gallery",
    "hike",
    "hiking",
    "landmark",
    "museum",
    "park",
    "see",
    "things to do",
    "trail",
    "visit",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("reelbot.scheduler")


@dataclass(frozen=True)
class NudgeConfig:
    interval_hours: float = 3.0
    cooldown_days: int = 3
    cluster_cooldown_days: int = 14
    min_items: int = 3
    recency_days: int = 7


@dataclass
class ClusterCandidate:
    key: str
    label: str
    kind: str
    items: list[dict[str, Any]]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def latest_activity_at(self) -> datetime:
        return max(item_datetime(item, "last_activity_at") for item in self.items)

    @property
    def sorted_items(self) -> list[dict[str, Any]]:
        return sorted(
            self.items,
            key=lambda item: (
                int(item.get("save_count") or 0),
                item_datetime(item, "last_activity_at"),
                item_datetime(item, "created_at"),
            ),
            reverse=True,
        )


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        LOG.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


def env_float(name: str, default: float, *, minimum: float = 0.01) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        LOG.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


def load_config() -> NudgeConfig:
    return NudgeConfig(
        interval_hours=env_float("NUDGE_INTERVAL_HOURS", 3.0),
        cooldown_days=env_int("NUDGE_COOLDOWN_DAYS", 3),
        cluster_cooldown_days=env_int("NUDGE_CLUSTER_COOLDOWN_DAYS", 14),
        min_items=env_int("NUDGE_MIN_ITEMS", 3),
        recency_days=env_int("NUDGE_RECENCY_DAYS", 7),
    )


def item_datetime(item: dict[str, Any], key: str) -> datetime:
    value = item.get(key)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def slug(value: str) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "saved"


def city_from_location_text(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None

    lowered = text.lower().replace(".", "")
    if re.search(r"(^|[^a-z])la([^a-z]|$)", lowered) or "los angeles" in lowered:
        return "Los Angeles"
    if "mumbai" in lowered or "bombay" in lowered:
        return "Mumbai"

    parts = [part.strip(" .'\"") for part in text.split(",") if part.strip(" .'\"")]
    city = parts[0] if parts else text
    return re.sub(r"\s+", " ", city).title() or None


def city_key_part(city: str) -> str:
    if city == "Los Angeles":
        return "la"
    return slug(city)


def city_label(city: str) -> str:
    if city == "Los Angeles":
        return "LA"
    return city


def category_bucket(item: dict[str, Any]) -> str | None:
    tags = item.get("tags") or []
    haystack = " ".join(
        [
            clean_text(item.get("category")),
            clean_text(item.get("list_name")),
            " ".join(clean_text(tag) for tag in tags),
        ]
    ).lower()
    if any(term in haystack for term in DINING_TERMS):
        return "dining"
    if any(term in haystack for term in ATTRACTION_TERMS):
        return "attraction"

    category = clean_text(item.get("category")).lower()
    return slug(category) if category else None


def category_label(category: str) -> str:
    if category == "dining":
        return "dining spots"
    if category == "attraction":
        return "things to do"
    return f"{category.replace('-', ' ')} spots"


def add_candidate(
    buckets: dict[str, ClusterCandidate],
    *,
    key: str,
    label: str,
    kind: str,
    item: dict[str, Any],
) -> None:
    if key not in buckets:
        buckets[key] = ClusterCandidate(key=key, label=label, kind=kind, items=[])
    buckets[key].items.append(item)


def build_candidates(items: list[dict[str, Any]]) -> list[ClusterCandidate]:
    buckets: dict[str, ClusterCandidate] = {}
    for item in items:
        city = city_from_location_text(item.get("location_text"))
        if city:
            city_part = city_key_part(city)
            add_candidate(
                buckets,
                key=city_part,
                label=f"{city_label(city)} spots",
                kind="city",
                item=item,
            )

            category = category_bucket(item)
            if category:
                add_candidate(
                    buckets,
                    key=f"{city_part}:{category}",
                    label=f"{city_label(city)} {category_label(category)}",
                    kind="city_category",
                    item=item,
                )

        list_name = clean_text(item.get("list_name"))
        if list_name:
            add_candidate(
                buckets,
                key=f"list:{slug(list_name)}",
                label=list_name,
                kind="list",
                item=item,
            )
    return list(buckets.values())


def qualifies(candidate: ClusterCandidate, config: NudgeConfig, blocked_cluster_keys: set[str]) -> bool:
    if candidate.key in blocked_cluster_keys:
        return False
    if candidate.count < config.min_items:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.recency_days)
    return candidate.latest_activity_at >= cutoff


def best_cluster(items: list[dict[str, Any]], config: NudgeConfig, blocked_cluster_keys: set[str]) -> ClusterCandidate | None:
    candidates = [
        candidate
        for candidate in build_candidates(items)
        if qualifies(candidate, config, blocked_cluster_keys)
    ]
    if not candidates:
        return None

    return max(candidates, key=lambda candidate: (candidate.count, candidate.latest_activity_at, candidate.key))


def response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif isinstance(block, dict) and block.get("text") is not None:
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def place_names(candidate: ClusterCandidate, limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in candidate.sorted_items:
        name = clean_text(item.get("place_name"))
        if name and name.lower() not in {existing.lower() for existing in names}:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def join_natural(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def fallback_body(candidate: ClusterCandidate) -> str:
    names = place_names(candidate, limit=3)
    if candidate.kind == "list":
        cluster_phrase = f"places saved in {candidate.label}"
    else:
        cluster_phrase = f"{candidate.label} saved"
    return (
        f"You've got {candidate.count} {cluster_phrase} - "
        f"{join_natural(names)}. Want me to help pick a plan?"
    )


def nudge_prompt(candidate: ClusterCandidate) -> str:
    names = place_names(candidate, limit=5)
    place_list = "\n".join(f"- {name}" for name in names)
    if candidate.kind == "list":
        cluster_description = f"the saved list named {candidate.label}"
    else:
        cluster_description = candidate.label

    return f"""
Write one proactive WhatsApp nudge for a saved-places bot.

Rules:
- Ground the message only in the saved items below.
- Mention 2 or 3 place names exactly as written.
- Do not invent places or facts about the places.
- Keep it casual, short, and under 240 characters.
- End with a light question that invites action.
- Return only the message text. No markdown.

Cluster: {cluster_description}
Saved item count in this cluster: {candidate.count}
Saved place names:
{place_list}
""".strip()


def generated_body_is_grounded(body: str, candidate: ClusterCandidate) -> bool:
    names = place_names(candidate, limit=5)
    if not body or len(body) > 320:
        return False
    if "http://" in body or "https://" in body:
        return False

    lowered = body.lower()
    mentioned = [name for name in names if name.lower() in lowered]
    return len(mentioned) >= min(2, len(names))


def generate_nudge_body(candidate: ClusterCandidate) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return fallback_body(candidate)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=120,
            temperature=0.4,
            system="You write grounded, concise WhatsApp copy. Never invent saved places.",
            messages=[{"role": "user", "content": nudge_prompt(candidate)}],
        )
        body = clean_text(response_text(response)).strip("\"'")
        if generated_body_is_grounded(body, candidate):
            return body
        LOG.warning("LLM nudge was not grounded enough; using deterministic fallback")
    except Exception as exc:
        LOG.warning("Nudge generation failed; using deterministic fallback: %s", exc)

    return fallback_body(candidate)


def process_group(conn: Any, group: dict[str, Any], config: NudgeConfig) -> bool:
    group_id = str(group["id"])
    chat_id = str(group["wa_chat_id"])

    if has_recent_nudge(conn, group_id, config.cooldown_days):
        LOG.info("Skipping group %s: nudge cooldown active", group_id)
        conn.commit()
        return False

    blocked_keys = recent_nudge_cluster_keys(conn, group_id, config.cluster_cooldown_days)
    cluster = best_cluster(saved_items_for_nudges(conn, group_id), config, blocked_keys)
    if cluster is None:
        LOG.info("Skipping group %s: no qualifying nudge cluster", group_id)
        conn.commit()
        return False

    body = generate_nudge_body(cluster)
    queued = enqueue_nudge(
        conn,
        group_id=group_id,
        chat_id=chat_id,
        cluster_key=cluster.key,
        body=body,
        cooldown_days=config.cooldown_days,
        cluster_cooldown_days=config.cluster_cooldown_days,
    )
    if queued:
        LOG.info("Queued nudge for group %s cluster=%s", group_id, cluster.key)
    else:
        LOG.info("Skipped group %s: cooldown became active before enqueue", group_id)
    return queued


def run_once(config: NudgeConfig) -> int:
    queued_count = 0
    with connect() as conn:
        groups = list_groups(conn)
        LOG.info("Scanning %s groups for nudges", len(groups))
        for group in groups:
            try:
                if process_group(conn, group, config):
                    queued_count += 1
            except Exception:
                conn.rollback()
                LOG.exception("Nudge scan failed for group %s", group.get("id"))
    return queued_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan saved places and enqueue rare proactive nudges.")
    parser.add_argument("--once", action="store_true", help="run one scheduler wake and exit")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    config = load_config()
    LOG.info(
        "Starting nudge scheduler interval=%sh cooldown=%sd cluster_cooldown=%sd min_items=%s recency=%sd",
        config.interval_hours,
        config.cooldown_days,
        config.cluster_cooldown_days,
        config.min_items,
        config.recency_days,
    )

    while True:
        try:
            queued_count = run_once(config)
            LOG.info("Nudge wake complete; queued=%s", queued_count)
        except Exception:
            LOG.exception("Nudge wake failed")

        if args.once:
            return 0
        time.sleep(config.interval_hours * 60 * 60)


if __name__ == "__main__":
    raise SystemExit(main())
