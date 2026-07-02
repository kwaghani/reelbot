from __future__ import annotations

import os
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

STALE_PROCESSING_MINUTES = int(os.getenv("JOB_RETRY_AFTER_MINUTES", "20"))
RETRY_ERROR_AFTER_MINUTES = int(os.getenv("JOB_RETRY_ERROR_AFTER_MINUTES", "5"))


def connect() -> psycopg.Connection:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing")
    return psycopg.connect(database_url, row_factory=dict_row)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def claim_next_job(conn: psycopg.Connection) -> dict[str, Any] | None:
    with conn.transaction():
        return conn.execute(
            """
            update jobs
               set status = 'processing',
                   updated_at = now()
             where id = (
               select id
                 from jobs
                where status = 'queued'
                   or (
                     status = 'processing'
                     and updated_at < now() - (%s * interval '1 minute')
                   )
                order by case when status = 'processing' then 0 else 1 end,
                         created_at
                for update skip locked
                limit 1
             )
            returning *
            """,
            (STALE_PROCESSING_MINUTES,),
        ).fetchone()


def requeue_retryable_ingest_errors(conn: psycopg.Connection, limit: int = 5) -> int:
    rows = conn.execute(
        """
        update jobs
           set status = 'queued',
               reply = 'Retrying with metadata fallback...',
               updated_at = now()
         where id in (
           select id
             from jobs
            where type = 'ingest'
              and status = 'error'
              and reply ilike %s
              and reply not ilike %s
              and created_at >= now() - interval '3 days'
              and updated_at < now() - (%s * interval '1 minute')
            order by updated_at asc
            limit %s
         )
        returning id
        """,
        (
            "Could not process this reel during ingest:%",
            "%Metadata fallback was tried%",
            RETRY_ERROR_AFTER_MINUTES,
            max(1, limit),
        ),
    ).fetchall()
    conn.commit()
    return len(rows)


def mark_job_done(conn: psycopg.Connection, job_id: str, reply: str) -> None:
    conn.execute(
        """
        update jobs
           set status = 'done',
               reply = %s,
               updated_at = now()
         where id = %s
        """,
        (reply, job_id),
    )
    conn.commit()


def mark_job_error(conn: psycopg.Connection, job_id: str, reply: str) -> None:
    conn.execute(
        """
        update jobs
           set status = 'error',
               reply = %s,
               updated_at = now()
         where id = %s
        """,
        (reply, job_id),
    )
    conn.commit()


def log_event(conn: psycopg.Connection, group_id: str | None, kind: str, detail: str | None = None) -> None:
    conn.execute(
        "insert into events (group_id, kind, detail) values (%s, %s, %s)",
        (group_id, kind, detail),
    )
    conn.commit()


def list_groups(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select id, wa_chat_id, name, created_at
          from groups
         where wa_chat_id is not null
         order by created_at
        """
    ).fetchall()
    return list(rows)


def has_recent_nudge(conn: psycopg.Connection, group_id: str, cooldown_days: int) -> bool:
    row = conn.execute(
        """
        select exists (
          select 1
            from nudges
           where group_id = %s
             and created_at >= now() - make_interval(days => %s)
        ) as exists
        """,
        (group_id, cooldown_days),
    ).fetchone()
    return bool(row and row["exists"])


def recent_nudge_cluster_keys(conn: psycopg.Connection, group_id: str, cooldown_days: int) -> set[str]:
    rows = conn.execute(
        """
        select distinct cluster_key
          from nudges
         where group_id = %s
           and created_at >= now() - make_interval(days => %s)
        """,
        (group_id, cooldown_days),
    ).fetchall()
    return {str(row["cluster_key"]) for row in rows}


def saved_items_for_nudges(conn: psycopg.Connection, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select i.id, i.place_name, i.category, i.location_text, i.tags, i.list_name,
               i.source_url, i.created_at,
               count(distinct s.member_id) as save_count,
               max(s.created_at) as last_saved_at,
               greatest(i.created_at, coalesce(max(s.created_at), i.created_at)) as last_activity_at
          from items i
          left join item_saves s on s.item_id = i.id
         where i.group_id = %s
           and nullif(btrim(i.place_name), '') is not null
         group by i.id
         order by greatest(i.created_at, coalesce(max(s.created_at), i.created_at)) desc,
                  count(distinct s.member_id) desc,
                  i.created_at desc
        """,
        (group_id,),
    ).fetchall()
    return list(rows)


def enqueue_nudge(
    conn: psycopg.Connection,
    *,
    group_id: str,
    chat_id: str,
    cluster_key: str,
    body: str,
    cooldown_days: int,
    cluster_cooldown_days: int,
) -> bool:
    if has_recent_nudge(conn, group_id, cooldown_days):
        conn.commit()
        return False
    if cluster_key in recent_nudge_cluster_keys(conn, group_id, cluster_cooldown_days):
        conn.commit()
        return False

    try:
        conn.execute(
            """
            insert into outbound_messages (group_id, chat_id, body, kind)
            values (%s, %s, %s, 'nudge')
            """,
            (group_id, chat_id, body),
        )
        conn.execute(
            """
            insert into nudges (group_id, cluster_key, body)
            values (%s, %s, %s)
            """,
            (group_id, cluster_key, body),
        )
        conn.execute(
            "insert into events (group_id, kind, detail) values (%s, 'nudge', %s)",
            (group_id, cluster_key),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def get_or_create_member(
    conn: psycopg.Connection,
    group_id: str,
    wa_user_id: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    row = conn.execute(
        """
        insert into members (group_id, wa_user_id, display_name)
        values (%s, %s, %s)
        on conflict (group_id, wa_user_id) do update
            set display_name = coalesce(excluded.display_name, members.display_name)
        returning *
        """,
        (group_id, wa_user_id, display_name),
    ).fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("Could not upsert member")
    return row


def upsert_item(
    conn: psycopg.Connection,
    *,
    group_id: str,
    source_url: str,
    place_id: str,
    place_name: str | None,
    category: str | None,
    location_text: str | None,
    lat: float | None,
    lng: float | None,
    price_tier: str | None,
    tags: list[str],
    list_name: str | None,
    transcript: str | None,
    embedding: list[float],
) -> dict[str, Any]:
    embedding_value = vector_literal(embedding)
    row = conn.execute(
        """
        insert into items (
          group_id, source_url, place_id, place_name, category, location_text,
          lat, lng, price_tier, tags, list_name, transcript, embedding
        )
        values (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s::vector
        )
        on conflict (group_id, place_id) do update
            set source_url = excluded.source_url,
                place_name = excluded.place_name,
                category = excluded.category,
                location_text = excluded.location_text,
                lat = excluded.lat,
                lng = excluded.lng,
                price_tier = excluded.price_tier,
                tags = excluded.tags,
                list_name = coalesce(items.list_name, excluded.list_name),
                transcript = excluded.transcript,
                embedding = excluded.embedding
        returning *
        """,
        (
            group_id,
            source_url,
            place_id,
            place_name,
            category,
            location_text,
            lat,
            lng,
            price_tier,
            tags,
            list_name,
            transcript,
            embedding_value,
        ),
    ).fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("Could not upsert item")
    return row


def add_item_save(conn: psycopg.Connection, item_id: str, member_id: str) -> None:
    conn.execute(
        """
        insert into item_saves (item_id, member_id)
        values (%s, %s)
        on conflict do nothing
        """,
        (item_id, member_id),
    )
    conn.commit()


def count_group_items(conn: psycopg.Connection, group_id: str) -> int:
    row = conn.execute("select count(*) as count from items where group_id = %s", (group_id,)).fetchone()
    return int(row["count"] if row else 0)


def _add_pattern_clause(clauses: list[str], params: list[Any], expressions: list[str], patterns: list[str]) -> None:
    if not expressions or not patterns:
        return

    pieces: list[str] = []
    for expression in expressions:
        pieces.extend(f"{expression} ilike %s" for _ in patterns)
        params.extend(patterns)
    clauses.append("(" + " or ".join(pieces) + ")")


def _add_tag_clause(clauses: list[str], params: list[Any], patterns: list[str]) -> None:
    if not patterns:
        return

    pieces = ["tag ilike %s" for _ in patterns]
    params.extend(patterns)
    clauses.append(
        """
        exists (
          select 1
            from unnest(coalesce(i.tags, array[]::text[])) tag
           where """
        + " or ".join(pieces)
        + "\n        )"
    )


def _category_patterns(category: str) -> list[str]:
    normalized = category.strip().lower()
    if normalized == "dining":
        return [
            "%restaurant%",
            "%dining%",
            "%food%",
            "%eat%",
            "%dinner%",
            "%lunch%",
            "%brunch%",
            "%cafe%",
            "%coffee%",
            "%bar%",
            "%bakery%",
            "%dessert%",
            "%sushi%",
            "%handroll%",
            "%italian%",
        ]
    if normalized == "attraction":
        return [
            "%attraction%",
            "%activity%",
            "%things to do%",
            "%visit%",
            "%see%",
            "%tourist%",
            "%theme park%",
            "%park%",
            "%beach%",
            "%pier%",
            "%museum%",
            "%gallery%",
            "%bookstore%",
            "%hike%",
            "%hiking%",
            "%trail%",
            "%view%",
            "%views%",
            "%landmark%",
            "%market%",
            "%show%",
            "%theater%",
            "%theatre%",
            "%garden%",
            "%outdoor%",
            "%nature%",
            "%walk%",
        ]
    return [f"%{category}%"]


def _filter_clauses(
    *,
    location: str | None = None,
    category: str | None = None,
    cuisine_or_tags: list[str] | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if location:
        normalized = location.strip().lower()
        if normalized == "los angeles":
            clauses.append(
                """
                (
                  i.location_text ilike %s
                  or i.location_text ~* %s
                )
                """
            )
            params.extend(["%Los Angeles%", r"(^|[^[:alpha:]])l\.?a\.?([^[:alpha:]]|$)"])
        elif normalized == "mumbai":
            clauses.append("(i.location_text ilike %s or i.location_text ilike %s)")
            params.extend(["%Mumbai%", "%Bombay%"])
        else:
            clauses.append("i.location_text ilike %s")
            params.append(f"%{location}%")

    if category:
        patterns = _category_patterns(category)
        category_clauses: list[str] = []
        category_params: list[Any] = []
        _add_pattern_clause(category_clauses, category_params, ["i.category", "i.list_name"], patterns)
        _add_tag_clause(category_clauses, category_params, patterns)
        if category_clauses:
            clauses.append("(" + " or ".join(category_clauses) + ")")
            params.extend(category_params)

    clean_tags = [tag.strip() for tag in cuisine_or_tags or [] if tag and tag.strip()]
    if clean_tags:
        _add_tag_clause(clauses, params, [f"%{tag}%" for tag in clean_tags])

    if not clauses:
        return "", params
    return " and " + " and ".join(clauses), params


ITEM_RESULT_FIELDS = """
       i.id, i.place_name, i.category, i.location_text, i.price_tier, i.tags, i.list_name,
       i.transcript, i.source_url, i.lat, i.lng, i.created_at
""".strip()


def count_filtered_items(
    conn: psycopg.Connection,
    *,
    group_id: str,
    location: str | None = None,
    category: str | None = None,
    cuisine_or_tags: list[str] | None = None,
) -> int:
    filter_sql, filter_params = _filter_clauses(
        location=location,
        category=category,
        cuisine_or_tags=cuisine_or_tags,
    )
    row = conn.execute(
        f"""
        select count(*) as count
          from items i
         where i.group_id = %s
           {filter_sql}
        """,
        [group_id, *filter_params],
    ).fetchone()
    return int(row["count"] if row else 0)


def search_items(
    conn: psycopg.Connection,
    *,
    group_id: str,
    embedding: list[float],
    limit: int = 8,
    location: str | None = None,
    category: str | None = None,
    cuisine_or_tags: list[str] | None = None,
    vector_limit: int = 24,
    location_hint: str | None = None,
) -> list[dict[str, Any]]:
    embedding_value = vector_literal(embedding)
    filter_sql, filter_params = _filter_clauses(
        location=location or location_hint,
        category=category,
        cuisine_or_tags=cuisine_or_tags,
    )
    vector_limit = max(limit, vector_limit)
    rows = conn.execute(
        f"""
        with ranked as (
          select {ITEM_RESULT_FIELDS},
                 i.embedding <=> %s::vector as distance
            from items i
           where i.group_id = %s
             and i.embedding is not null
             {filter_sql}
           order by i.embedding <=> %s::vector
           limit %s
        )
        select r.id, r.place_name, r.category, r.location_text, r.price_tier, r.tags,
               r.list_name, r.transcript, r.source_url, r.lat, r.lng, r.created_at,
               r.distance,
               count(distinct s.member_id) as save_count,
               max(s.created_at) as last_saved_at
          from ranked r
          left join item_saves s on s.item_id = r.id
         group by r.id, r.place_name, r.category, r.location_text, r.price_tier, r.tags,
                  r.list_name, r.transcript, r.source_url, r.lat, r.lng, r.created_at,
                  r.distance
         order by count(distinct s.member_id) desc,
                  max(s.created_at) desc nulls last,
                  r.distance asc
         limit %s
        """,
        [embedding_value, group_id, *filter_params, embedding_value, vector_limit, limit],
    ).fetchall()
    return list(rows)


def recent_items(conn: psycopg.Connection, group_id: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select i.id, i.place_name, i.category, i.location_text, i.price_tier, i.tags, i.list_name,
               i.transcript, i.source_url, i.lat, i.lng, i.created_at,
               1.0 as distance,
               count(distinct s.member_id) as save_count,
               max(s.created_at) as last_saved_at
          from items i
          left join item_saves s on s.item_id = i.id
         where i.group_id = %s
         group by i.id
         order by i.created_at desc
         limit %s
        """,
        (group_id, limit),
    ).fetchall()
    return list(rows)


def group_location_stats(conn: psycopg.Connection, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select i.location_text,
               count(distinct i.id) as item_count,
               count(distinct s.member_id) as save_count,
               max(s.created_at) as last_saved_at
          from items i
          left join item_saves s on s.item_id = i.id
         where i.group_id = %s
         group by i.location_text
         order by count(distinct s.member_id) desc,
                  count(distinct i.id) desc,
                  max(s.created_at) desc nulls last
        """,
        (group_id,),
    ).fetchall()
    return list(rows)


def lookup_items_by_name(
    conn: psycopg.Connection,
    *,
    group_id: str,
    target_place: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    target = target_place.strip()
    if not target:
        return []

    rows = conn.execute(
        """
        select i.id, i.place_name, i.category, i.location_text, i.price_tier, i.tags,
               i.list_name, i.transcript, i.source_url, i.lat, i.lng, i.created_at,
               0.0 as distance,
               count(distinct s.member_id) as save_count,
               max(s.created_at) as last_saved_at
          from items i
          left join item_saves s on s.item_id = i.id
         where i.group_id = %s
           and i.place_name is not null
           and (
             i.place_name ilike %s
             or %s ilike ('%%' || i.place_name || '%%')
           )
         group by i.id
         order by (lower(i.place_name) = lower(%s)) desc,
                  count(distinct s.member_id) desc,
                  max(s.created_at) desc nulls last,
                  i.created_at desc
         limit %s
        """,
        (group_id, f"%{target}%", target, target, limit),
    ).fetchall()
    return list(rows)


def saved_items_for_summary(conn: psycopg.Connection, group_id: str, limit: int = 40) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select i.id, i.place_name, i.category, i.location_text, i.price_tier, i.tags,
               i.list_name, i.transcript, i.source_url, i.lat, i.lng, i.created_at,
               1.0 as distance,
               count(distinct s.member_id) as save_count,
               max(s.created_at) as last_saved_at
          from items i
          left join item_saves s on s.item_id = i.id
         where i.group_id = %s
         group by i.id
         order by count(distinct s.member_id) desc,
                  max(s.created_at) desc nulls last,
                  i.created_at desc
         limit %s
        """,
        (group_id, limit),
    ).fetchall()
    return list(rows)
