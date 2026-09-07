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


def migrate_legacy(database_url, dry_run=False):
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
        if not exists(conn,'items') and (exists(conn,'user_places') or exists(conn,'entries')):
            conn.execute('create table if not exists schema_migrations(id text primary key,applied_at timestamptz not null default now(),report jsonb not null)')
            relation='entries' if exists(conn,'entries') else 'user_places'
            counts=conn.execute(sql.SQL("select legacy_item_id,count(*) as n from {} where legacy_item_id is not null group by legacy_item_id").format(sql.Identifier(relation))).fetchall()
            orphan=conn.execute('select count(*) as n from orphaned_items').fetchone()['n']
            report={'total_items':len(counts)+orphan,'single_identifiable_saver':sum(r['n']==1 for r in counts),
                'multiple_savers':sum(r['n']>1 for r in counts),'no_resolvable_saver':orphan,
                'expected_personal_rows':sum(r['n'] for r in counts),'personal_rows':sum(r['n'] for r in counts),
                'orphan_rows':orphan,'accounted_items':len(counts)+orphan,'existing_personal_schema':True}
            conn.execute('insert into schema_migrations(id,report) values(%s,%s)',(VERSION,Jsonb(report)))
            return report
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


def harden_registry(conn):
    version='003_registry_function_path'
    if not conn.execute('select 1 from schema_migrations where id=%s',(version,)).fetchone():
        conn.execute((ROOT/'db/migrations/003_registry_function_path.sql').read_text(),prepare=False)
        conn.execute('insert into schema_migrations(id,report) values(%s,%s)',(version,Jsonb({'validation_search_path_fixed':True})))


def run(database_url, dry_run=False):
    with psycopg.connect(database_url,row_factory=dict_row,connect_timeout=10) as conn:
        if dry_run:
            conn.execute('set transaction read only')
            result=inventory(conn)
            if exists(conn,'schema_migrations'):
                previous=conn.execute('select report from schema_migrations where id=%s',(VERSION,)).fetchone()
                if previous: result={**previous['report'],'ownership_previously_migrated':True}
            relation='entries' if exists(conn,'entries') else 'user_places' if exists(conn,'user_places') else None
            result['existing_personal_entries']=conn.execute(sql.SQL('select count(*) as n from {}').format(sql.Identifier(relation))).fetchone()['n'] if relation else 0
            result['content_migration_applied']=exists(conn,'entries')
            conn.rollback(); return result
    legacy=migrate_legacy(database_url)
    with psycopg.connect(database_url,row_factory=dict_row,connect_timeout=10) as conn:
        conn.execute('select pg_advisory_xact_lock(735002)')
        prior=conn.execute("select report from schema_migrations where id='002_content_entries'").fetchone()
        if prior:
            harden_registry(conn)
            return {**legacy,**prior['report'],'validation_search_path_fixed':True,'already_migrated':True}
        conn.execute('create table if not exists content_type_registry(key text primary key,spec jsonb not null)')
        from worker.registry import sync_registry
        data=sync_registry(conn)
        already_entries=exists(conn,'entries')
        relation='entries' if already_entries else 'user_places'
        ownership_before=conn.execute(sql.SQL('select id,user_id,save_id from {} order by id').format(sql.Identifier(relation))).fetchall()
        before=len(ownership_before)
        if not already_entries: conn.execute((ROOT/'db/migrations/002_content_entries.sql').read_text(),prepare=False)
        conn.execute((ROOT/'db/schema.sql').read_text(),prepare=False)
        from worker.db import file_entry
        rows=conn.execute('select * from entries').fetchall()
        for row in rows:
            place=conn.execute('select * from places where id=%s',(row['place_id'],)).fetchone() if row['place_id'] else None
            file_entry(conn,row,place,data)
        after=conn.execute('select count(*) as n from entries').fetchone()['n']
        ownership_after=conn.execute('select id,user_id,save_id from entries order by id').fetchall()
        if ownership_before!=ownership_after: raise RuntimeError('Entry ownership/accounting failed; content migration rolled back')
        all_places=all(row['content_type']=='place' for row in rows)
        report={'previous_personal_rows':before,'entries_after':after,'all_existing_rows_typed_place':all_places,
                'owners_preserved':True,'registry_types':len(data)}
        conn.execute("insert into schema_migrations(id,report) values('002_content_entries',%s)",(Jsonb(report),))
        harden_registry(conn)
        return {**legacy,**report,'validation_search_path_fixed':True,'already_migrated':False}


if __name__ == "__main__":
    load_dotenv(ROOT/".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-env", default="DATABASE_URL")
    args = parser.parse_args()
    if not os.getenv(args.database_env):
        raise SystemExit(f"{args.database_env} is missing")
    print(json.dumps(run(os.environ[args.database_env], args.dry_run), indent=2, default=str))
