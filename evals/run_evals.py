from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "worker"
sys.path.insert(0, str(WORKER_DIR))

from db import connect  # noqa: E402
from retrieval import (  # noqa: E402
    ATTRACTION_TERMS,
    DINING_TERMS,
    city_short_name,
    compose_answer,
    item_city,
    retrieve_for_query,
)

load_dotenv(ROOT / ".env")

QUERIES_PATH = Path(__file__).with_name("queries.jsonl")
ATTRACTION_MARKERS = {
    "bookstore",
    "griffith",
    "hike",
    "hiking",
    "last bookstore",
    "museum",
    "park",
    "pier",
    "santa monica",
    "trail",
    "universal",
    "venice beach",
}


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in QUERIES_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def choose_group_id() -> tuple[str, str]:
    env_group_id = os.getenv("EVAL_GROUP_ID", "").strip()
    if env_group_id:
        return env_group_id, "EVAL_GROUP_ID"

    with connect() as conn:
        row = conn.execute(
            """
            select g.id, coalesce(g.name, g.wa_chat_id, g.id::text) as label,
                   count(i.id) as item_count
              from groups g
              left join items i on i.group_id = g.id
             group by g.id
             order by count(i.id) desc, g.created_at desc
             limit 1
            """
        ).fetchone()

    if row is None or int(row.get("item_count") or 0) == 0:
        raise RuntimeError("No eval group found. Set EVAL_GROUP_ID or ingest saved items first.")
    return str(row["id"]), f"{row['label']} ({row['item_count']} items)"


def check_field(actual: Any, expected: Any) -> bool:
    return actual == expected


def item_blob(item: dict[str, Any]) -> str:
    values = [
        item.get("place_name"),
        item.get("category"),
        item.get("location_text"),
        item.get("list_name"),
        " ".join(item.get("tags") or []),
    ]
    return " ".join(str(value) for value in values if value).lower()


def item_looks_dining(item: dict[str, Any]) -> bool:
    blob = item_blob(item)
    return any(term in blob for term in DINING_TERMS)


def item_looks_attraction(item: dict[str, Any]) -> bool:
    blob = item_blob(item)
    return any(term in blob for term in ATTRACTION_TERMS | ATTRACTION_MARKERS)


def names(result_items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("place_name") or "").strip() for item in result_items if item.get("place_name")]


def outcome_passed(case: dict[str, Any], result: Any, answer: str) -> bool:
    expect = str(case.get("expect") or "").lower()
    expected_location = case.get("location")
    result_names = [name.lower() for name in names(result.items)]

    if "grounded-empty" in expect:
        return (
            result.empty_reason == "location_empty"
            and expected_location in answer
            and "We don't have any saved places" in answer
        )

    if case.get("target_place"):
        target = str(case["target_place"]).lower()
        return (
            result.slots.intent == "lookup"
            and result.target_item is not None
            and target in str(result.target_item.get("place_name") or "").lower()
            and "Yes" in answer
        )

    if case.get("intent") == "meta":
        return result.slots.intent == "meta" and bool(result.items) and answer.startswith("Saved so far:")

    if "defaults to dominant city" in expect:
        return (
            result.defaulted_location == "Los Angeles"
            and bool(result.items)
            and answer.startswith("Assuming LA")
        )

    if "italian place surfaced" in expect:
        return (
            result.defaulted_location == "Los Angeles"
            and any("sugo social" in name or "italian" in item_blob(item) for name, item in zip(result_names, result.items))
            and answer.startswith("Assuming LA")
        )

    if expected_location:
        if not result.items or any(item_city(item) != expected_location for item in result.items):
            return False

    if case.get("category") == "dining":
        return bool(result.items) and any(item_looks_dining(item) for item in result.items)
    if case.get("category") == "attraction":
        return bool(result.items) and any(item_looks_attraction(item) for item in result.items)

    return bool(result.items)


def marker(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def truncate(text: str, limit: int = 38) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def main() -> int:
    group_id, group_label = choose_group_id()
    cases = load_cases()

    print(f"Eval group: {group_label}")
    print("")
    print(
        f"{'#':>2}  {'Query':<38}  {'Intent':<6}  {'Location':<8}  {'Category':<8}  {'Outcome':<7}  Names"
    )
    print("-" * 112)

    check_total = 0
    check_passed = 0
    case_passed = 0

    for index, case in enumerate(cases, start=1):
        result = retrieve_for_query(group_id, case["q"])
        answer = compose_answer(result)

        checks = [
            check_field(result.slots.intent, case.get("intent")),
            check_field(result.slots.location, case.get("location")),
            check_field(result.slots.category, case.get("category")),
        ]
        if "target_place" in case:
            checks.append(check_field(result.slots.target_place, case.get("target_place")))
        if "cuisine" in case:
            checks.append(str(case["cuisine"]).lower() in (result.slots.cuisine_or_tags or []))

        outcome_ok = outcome_passed(case, result, answer)
        checks.append(outcome_ok)

        check_total += len(checks)
        check_passed += sum(1 for ok in checks if ok)
        row_ok = all(checks)
        case_passed += int(row_ok)

        print(
            f"{index:>2}  {truncate(case['q']):<38}  "
            f"{marker(checks[0]):<6}  {marker(checks[1]):<8}  {marker(checks[2]):<8}  "
            f"{marker(outcome_ok):<7}  {', '.join(names(result.items)[:4]) or result.empty_reason or '-'}"
        )
        if not row_ok:
            print(f"    parsed={result.slots} answer={answer}")

    print("-" * 112)
    print(f"Cases passed: {case_passed}/{len(cases)}")
    print(f"Checks passed: {check_passed}/{check_total}")
    return 0 if case_passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
