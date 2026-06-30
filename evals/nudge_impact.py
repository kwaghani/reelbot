from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

from db import connect  # noqa: E402


def impact_window_hours() -> int:
    raw = os.getenv("NUDGE_IMPACT_WINDOW_HOURS", "").strip()
    if not raw:
        return 24
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def truncate(text: str | None, limit: int = 64) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def main() -> int:
    load_dotenv(ROOT / ".env")
    hours = impact_window_hours()

    with connect() as conn:
        rows = conn.execute(
            """
            with outcomes as (
              select n.id,
                     n.group_id,
                     coalesce(g.name, g.wa_chat_id) as group_name,
                     n.cluster_key,
                     n.body,
                     n.created_at as nudged_at,
                     count(e.id) filter (where e.kind in ('query', 'save')) as engagement_events,
                     min(e.created_at) filter (where e.kind in ('query', 'save')) as first_engaged_at
                from nudges n
                join groups g on g.id = n.group_id
                left join events e
                  on e.group_id = n.group_id
                 and e.kind in ('query', 'save')
                 and e.created_at > n.created_at
                 and e.created_at <= n.created_at + make_interval(hours => %s)
               group by n.id, n.group_id, g.name, g.wa_chat_id, n.cluster_key, n.body, n.created_at
            )
            select *,
                   engagement_events > 0 as engaged
              from outcomes
             order by nudged_at desc
            """,
            (hours,),
        ).fetchall()

    total = len(rows)
    converted = sum(1 for row in rows if row["engaged"])
    rate = (converted / total) if total else 0.0

    print(f"Nudge impact window: {hours}h")
    print(f"Nudges: {total}  engaged: {converted}  conversion_rate: {rate:.1%}")
    if not rows:
        return 0

    print("")
    print("nudged_at | group | cluster | engaged | events | first_engaged_at | body")
    print("-" * 116)
    for row in rows:
        print(
            " | ".join(
                [
                    str(row["nudged_at"]),
                    truncate(row["group_name"], 24),
                    truncate(row["cluster_key"], 20),
                    "yes" if row["engaged"] else "no",
                    str(row["engagement_events"]),
                    str(row["first_engaged_at"] or ""),
                    truncate(row["body"], 70),
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
