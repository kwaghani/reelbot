from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from db import connect, group_folders, vector_literal
from embed import embed_document
from worker import assign_folders, synthesize_embedding_text

ROOT = Path(__file__).resolve().parents[1]


def item_for_classification(row: dict[str, Any]) -> dict[str, Any]:
    category = str(row.get("category") or "").strip()
    return {
        "title": row.get("place_name"),
        "place_name": row.get("place_name"),
        "content_type": category.lower(),
        "category": category,
        "location_text": row.get("location_text"),
        "tags": row.get("tags") or [],
        "transcript": row.get("transcript"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reclassify saved items with the current folder model and rebuild their embeddings."
    )
    parser.add_argument("--group-id", default=os.getenv("TEST_GROUP_ID", ""))
    parser.add_argument("--limit", type=int, default=0, help="0 means every item in the group")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the proposed folders and embeddings. The default is a dry run.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    if not args.group_id:
        raise SystemExit("Set TEST_GROUP_ID or pass --group-id.")

    with connect() as conn:
        sql = """
            select id, place_name, category, location_text, tags, list_name,
                   subfolder, transcript
              from items
             where group_id = %s
             order by created_at
        """
        params: list[Any] = [args.group_id]
        if args.limit > 0:
            sql += " limit %s"
            params.append(args.limit)
        rows = list(conn.execute(sql, params).fetchall())
        existing_folders = group_folders(conn, args.group_id)

        changed = 0
        for row in rows:
            item = item_for_classification(row)
            folder, subfolder = assign_folders(item, existing_folders=existing_folders)
            existing = next(
                (
                    candidate
                    for candidate in existing_folders
                    if str(candidate.get("folder") or "").casefold() == folder.casefold()
                ),
                None,
            )
            if existing is None:
                existing_folders.append(
                    {"folder": folder, "subfolders": [subfolder] if subfolder else []}
                )
            elif subfolder and subfolder not in (existing.get("subfolders") or []):
                existing.setdefault("subfolders", []).append(subfolder)
            old = f"{row.get('list_name') or 'Other'} / {row.get('subfolder') or '-'}"
            new = f"{folder} / {subfolder or '-'}"
            marker = "CHANGE" if old != new else "KEEP"
            print(f"{marker:6} {row.get('place_name')}: {old} -> {new}")
            if old == new:
                continue
            changed += 1
            if args.apply:
                vector = embed_document(synthesize_embedding_text(item, folder, subfolder))
                conn.execute(
                    """
                    update items
                       set list_name = %s,
                           subfolder = %s,
                           embedding = %s::vector
                     where id = %s
                    """,
                    (folder, subfolder, vector_literal(vector), row["id"]),
                )

        if args.apply:
            conn.commit()
        print(f"\n{'Applied' if args.apply else 'Proposed'} {changed} change(s) across {len(rows)} item(s).")
        if not args.apply and changed:
            print("Run again with --apply after reviewing this output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
