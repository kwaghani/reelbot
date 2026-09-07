"""Atomic forward-only ownership migration. Restore a database backup to undo."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
VERSION = "001_single_user"


def exists(conn, name):
    return conn.execute("select to_regclass(%s) as name", ("public."+name,)).fetchone()["name"] is not None


def foreign_column(conn, source, target):
    """Read historical FK names from the catalog, including older schema variants."""
    rows = conn.execute(
        """select a.attname from pg_constraint c join pg_attribute a
        on a.attrelid=c.conrelid and a.attnum=any(c.conkey)
        where c.contype='f' and c.conrelid=to_regclass(%s)
        and c.confrelid=to_regclass(%s)""", (source, target)).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"Expected one ownership reference from {source} to {target}")
    return rows[0]["attname"]


def inventory(conn):
    owners = {}
    unresolved = 0
    count = conn.execute("select count(*) as n from items").fetchone()["n"] if exists(conn, "items") else 0
    if count and exists(conn, "app_devices") and exists(conn, "item_saves"):
        item_col = foreign_column(conn, "item_saves", "items")
        saver_col = foreign_column(conn, "item_saves", "members")
        query = sql.SQL(
            "select s.{item}::text as item,d.id::text as owner from item_saves s "
            "join members m on m.id=s.{saver} left join app_devices d on d.id::text=m.wa_user_id"
        ).format(item=sql.Identifier(item_col), saver=sql.Identifier(saver_col))
        for row in conn.execute(query).fetchall():
            if row["owner"]:
                owners.setdefault(row["item"], set()).add(row["owner"])
            else:
                unresolved += 1
    single = sum(len(v) == 1 for v in owners.values())
    multiple = sum(len(v) > 1 for v in owners.values())
    return dict(total_items=count, single_identifiable_saver=single,
                multiple_savers=multiple, no_resolvable_saver=count-single-multiple,
                expected_personal_rows=sum(map(len, owners.values())),
                unresolved_saver_associations=unresolved)


def run(database_url, dry_run=False):
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10) as conn:
        if dry_run:
            conn.execute("set transaction read only")
            report = inventory(conn)
            conn.rollback()
            return report
        conn.execute("select pg_advisory_xact_lock(735001)")
        if exists(conn, "schema_migrations"):
            prior = conn.execute("select report from schema_migrations where id=%s", (VERSION,)).fetchone()
            if prior:
                return {**prior["report"], "already_migrated": True}
        for name in ["items", "item_saves", "members", "app_devices", "jobs", "events", "groups"]:
            if exists(conn, name):
                conn.execute(sql.SQL("lock table {} in access exclusive mode").format(sql.Identifier(name)))
        report = inventory(conn)
        conn.execute((ROOT/"db/migrations/001_single_user.sql").read_text(), prepare=False)
        result = conn.execute(
            """select (select count(*) from user_places where legacy_item_id is not null) as personal_rows,
            (select count(*) from orphaned_items) as orphan_rows,
            (select count(distinct original) from (
              select legacy_item_id as original from user_places where legacy_item_id is not null
              union select original_item_id from orphaned_items) accounted) as accounted_items"""
        ).fetchone()
        if result["accounted_items"] != report["total_items"] or result["personal_rows"] != report["expected_personal_rows"]:
            raise RuntimeError("Ownership/accounting validation failed; entire migration rolled back")
        report.update(result)
        conn.execute("insert into schema_migrations(id,report) values(%s,%s)", (VERSION, Jsonb(report)))
        conn.commit()
        return report


if __name__ == "__main__":
    load_dotenv(ROOT/".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-env", default="DATABASE_URL")
    args = parser.parse_args()
    if not os.getenv(args.database_env):
        raise SystemExit(f"{args.database_env} is missing")
    print(json.dumps(run(os.environ[args.database_env], args.dry_run), indent=2, default=str))
