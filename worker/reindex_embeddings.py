from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from db import connect, vector_literal
from embed import MODEL_NAME, embed_document
from worker import synthesize_embedding_text

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild saved-item embeddings with the configured model.")
    parser.add_argument("--group-id", default=os.getenv("TEST_GROUP_ID", ""))
    parser.add_argument("--all-groups", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Write vectors; default is a dry run.")
    return parser.parse_args()


def embedding_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": row.get("place_name"),
        "place_name": row.get("place_name"),
        "category": row.get("category"),
        "content_type": row.get("category"),
        "location_text": row.get("location_text"),
        "price_tier": row.get("price_tier"),
        "tags": row.get("tags") or [],
        "list_name": row.get("list_name"),
        "subfolder": row.get("subfolder"),
        "transcript": row.get("transcript"),
    }


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    if not args.all_groups and not args.group_id:
        raise SystemExit("Set TEST_GROUP_ID, pass --group-id, or use --all-groups.")

    where = "" if args.all_groups else "where group_id = %s"
    params = () if args.all_groups else (args.group_id,)
    with connect() as conn:
        rows = list(
            conn.execute(
                f"""
                select id, place_name, category, location_text, price_tier, tags,
                       list_name, subfolder, transcript
                  from items
                  {where}
                 order by created_at
                """,
                params,
            ).fetchall()
        )
        if not rows:
            print("No items to reindex.")
            return 0

        print(f"Embedding model: {MODEL_NAME}")
        print(f"Items: {len(rows)}")
        if not args.apply:
            print("Dry run only. Add --apply to write rebuilt vectors.")
            return 0

        for index, row in enumerate(rows, start=1):
            item = embedding_item(row)
            vector = embed_document(
                synthesize_embedding_text(item, row.get("list_name"), row.get("subfolder"))
            )
            if len(vector) != 384:
                raise RuntimeError(f"{MODEL_NAME} returned {len(vector)} dimensions; schema requires 384")
            conn.execute(
                "update items set embedding = %s::vector where id = %s",
                (vector_literal(vector), row["id"]),
            )
            print(f"[{index}/{len(rows)}] {row.get('place_name') or row['id']}")
        conn.commit()
    print("Reindex complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
