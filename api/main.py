from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from reel_urls import canonical_reel_url
from db import connect, get_or_create_member, log_event  # noqa: E402

load_dotenv(ROOT / ".env")

LOG = logging.getLogger("reelbot.api")
INGEST_DRAIN_LOCK = threading.Lock()
QUERY_WAIT_SECONDS = 30
QUERY_POLL_SECONDS = 0.5


def api_drain_enabled() -> bool:
    """When true (local/single-process mode) the API answers queries and drains
    ingest jobs itself, which loads the ML stack into this process. In
    production a dedicated worker owns all jobs and this must be off so the
    web service stays small enough for its instance size."""
    value = os.getenv("REELBOT_API_DRAIN_JOBS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}



class Settings(BaseModel):
    api_key: str
    test_group_id: str

    @field_validator("api_key")
    @classmethod
    def api_key_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("API_KEY is missing")
        return value

    @field_validator("test_group_id")
    @classmethod
    def group_id_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("TEST_GROUP_ID is missing")
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("TEST_GROUP_ID must be a UUID") from exc
        return str(UUID(value))


JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def clean_device_id(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", value):
        raise ValueError("device_id is invalid")
    return value


class ShareRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    user_name: str = Field(min_length=1, max_length=120)
    device_id: str | None = Field(default=None, max_length=64)
    group_id: str | None = Field(default=None, max_length=40)
    request_id: str | None = Field(default=None, min_length=8, max_length=100)

    @field_validator("url")
    @classmethod
    def clean_supported_url(cls, value: str) -> str:
        return canonical_reel_url(value)

    @field_validator("user_name")
    @classmethod
    def clean_user_name(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("user_name is required")
        return value


class ChatTurn(BaseModel):
    role: str
    text: str = Field(min_length=1, max_length=1500)

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        return value


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    user_name: str = Field(min_length=1, max_length=120)
    history: list[ChatTurn] = Field(default_factory=list, max_length=16)
    device_id: str | None = Field(default=None, max_length=64)
    group_id: str | None = Field(default=None, max_length=40)
    request_id: str | None = Field(default=None, min_length=8, max_length=100)

    @field_validator("text", "user_name")
    @classmethod
    def clean_text_field(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("field is required")
        return value


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    user_name: str = Field(min_length=1, max_length=120)
    device_id: str = Field(min_length=8, max_length=64)

    @field_validator("name", "user_name")
    @classmethod
    def clean_group_field(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("field is required")
        return value


class GroupJoinRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    user_name: str = Field(min_length=1, max_length=120)
    device_id: str = Field(min_length=8, max_length=64)

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        value = re.sub(r"[\s-]+", "", value).upper()
        if not re.fullmatch(r"[A-Z2-9]{6}", value):
            raise ValueError("Enter the six-character invite code")
        return value


class GroupItemsAddRequest(BaseModel):
    source_group_id: str = Field(min_length=36, max_length=36)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    user_name: str = Field(min_length=1, max_length=120)
    device_id: str = Field(min_length=8, max_length=64)

    @field_validator("source_group_id")
    @classmethod
    def valid_source_group_id(cls, value: str) -> str:
        value = value.strip()
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("source_group_id must be a UUID") from exc
        return value

    @field_validator("item_ids")
    @classmethod
    def valid_item_ids(cls, values: list[str]) -> list[str]:
        unique: list[str] = []
        for value in values:
            try:
                normalized = str(UUID(value))
            except ValueError as exc:
                raise ValueError("item_ids must contain UUIDs") from exc
            if normalized not in unique:
                unique.append(normalized)
        if not unique:
            raise ValueError("item_ids is required")
        return unique

    @field_validator("user_name")
    @classmethod
    def clean_transfer_user_name(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("user_name is required")
        return value


class GroupResponse(BaseModel):
    id: str
    name: str
    join_code: str | None
    member_count: int = 0
    item_count: int = 0


class ShareResponse(BaseModel):
    status: str
    job_id: str


class DeviceRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=120)

    @field_validator("user_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("Name is required")
        return value


class JobResponse(BaseModel):
    id: str
    status: str
    message: str | None = None
    item_id: str | None = None
    answer: str | None = None
    sources: list[QuerySource] = Field(default_factory=list)


class GroupItemsAddResponse(BaseModel):
    added: int
    already_present: int


class QuerySource(BaseModel):
    title: str
    url: str


class QueryResponse(BaseModel):
    answer: str = ""
    sources: list[QuerySource] = Field(default_factory=list)
    status: str = "done"
    job_id: str | None = None


class ItemResponse(BaseModel):
    id: str | None = None
    place_name: str | None
    category: str | None
    location_text: str | None
    list_name: str | None
    subfolder: str | None = None
    source_url: str | None = None
    status: str = "saved"
    message: str | None = None
    lat: float | None
    lng: float | None
    price_tier: str | None
    tags: list[str]
    save_count: int
    job_id: str | None = None
    created_at: str | None = None


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings(
        api_key=os.getenv("API_KEY", ""),
        test_group_id=os.getenv("TEST_GROUP_ID", ""),
    )


def settings() -> Settings:
    try:
        return load_settings()
    except ValidationError as exc:
        LOG.error("API settings are invalid")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured",
        ) from exc


def require_api_key(
    x_api_key: str | None = Header(default=None),
    app_settings: Settings = Depends(settings),
) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, app_settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


app = FastAPI(
    title="Shared Reel Bot API",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Any, exc: RequestValidationError) -> JSONResponse:
    LOG.info("Invalid request body")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "; ".join(str(error["msg"]).removeprefix("Value error, ") for error in exc.errors())},
    )


@app.exception_handler(ValueError)
async def value_error_handler(_request: Any, exc: ValueError) -> JSONResponse:
    LOG.info("Bad request: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc) or "Bad request"},
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(_request: Any, exc: Exception) -> JSONResponse:
    LOG.exception("API request failed: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Server error"},
    )


JobResponse.model_rebuild()


def require_device(authorization: str | None = Header(default=None), _key: None = Depends(require_api_key)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Open ReelBot to set up this device first.")
    token = authorization[7:]
    if not 32 <= len(token) <= 128:
        raise HTTPException(status_code=401, detail="Invalid device session")
    with connect() as conn:
        row = conn.execute("select id::text as id from app_devices where token_hash = %s",
                           (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="This device session is no longer valid. Set up the app again.")
    return row["id"]


def bound_device(claimed: str | None, authenticated: str) -> str:
    if claimed and claimed != authenticated:
        raise HTTPException(status_code=403, detail="Device does not match this session")
    return authenticated


@app.post("/devices", status_code=201, dependencies=[Depends(require_api_key)])
def register_device(body: DeviceRequest) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        row = conn.execute(
            "insert into app_devices(token_hash, display_name) values (%s, %s) returning id::text as id",
            (hashlib.sha256(token.encode()).hexdigest(), body.user_name),
        ).fetchone()
    return {"device_id": row["id"], "token": token}


def generate_join_code(conn: Any) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(6))
        exists = conn.execute("select 1 from groups where join_code = %s", (code,)).fetchone()
        if not exists:
            return code
    raise RuntimeError("Could not generate a unique join code")


def resolve_group_id(
    conn: Any,
    app_settings: Settings,
    *,
    group_id: str | None,
    device_id: str | None,
    user_name: str,
) -> str:
    """Default to the shared test group (auto-membership, as before). Any
    other group requires the device to be a member."""
    if group_id:
        try:
            group_id = str(UUID(group_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid group id") from exc
    if not group_id or group_id == app_settings.test_group_id:
        ensure_test_group(conn, app_settings.test_group_id)
        get_or_create_member(
            conn,
            app_settings.test_group_id,
            wa_user_id=device_id or user_name,
            display_name=user_name if user_name != "Friend" else None,
        )
        return app_settings.test_group_id

    try:
        UUID(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group id") from exc
    if not device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this group")
    member = conn.execute(
        "select 1 from members where group_id = %s and wa_user_id = %s",
        (group_id, device_id),
    ).fetchone()
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this group")
    return group_id


def group_response_rows(conn: Any, group_ids: list[str]) -> list[dict[str, Any]]:
    if not group_ids:
        return []
    rows = conn.execute(
        """
        select g.id::text as id,
               coalesce(g.name, 'Group') as name,
               g.join_code,
               (select count(*) from members m where m.group_id = g.id)::int as member_count,
               (select count(*) from items i where i.group_id = g.id)::int as item_count
          from groups g
         where g.id = any(%s::uuid[])
         order by g.created_at
        """,
        (group_ids,),
    ).fetchall()
    return list(rows)


def copy_items_between_groups(
    conn: Any,
    *,
    source_group_id: str,
    target_group_id: str,
    item_ids: list[str],
    member_id: str,
) -> tuple[int, int]:
    """Copy selected saved items into another member-visible group.

    Source records stay in place. Existing target records are reused by place
    identity or source URL, so repeatedly adding the same reel is idempotent.
    """
    # Serialize group writes, including null place identities and deletion.
    conn.execute("select id from groups where id = any(%s::uuid[]) order by id for update", ([source_group_id, target_group_id],))
    conn.execute("select id from items where group_id = %s and id = any(%s::uuid[]) for share", (source_group_id, item_ids)).fetchall()
    found_row = conn.execute(
        """
        select count(*)::int as count
          from items
         where group_id = %s
           and id = any(%s::uuid[])
        """,
        (source_group_id, item_ids),
    ).fetchone()
    found = int(found_row["count"] if found_row else 0)

    if found != len(item_ids):
        raise HTTPException(status_code=404, detail="Some selected reels no longer exist. Refresh and select them again.")
    inserted = conn.execute(
        """
        insert into items (
          group_id, source_url, place_id, place_name, category, location_text,
          lat, lng, price_tier, tags, list_name, subfolder, transcript, embedding
        )
        select %s, source.source_url, source.place_id, source.place_name,
               source.category, source.location_text, source.lat, source.lng,
               source.price_tier, source.tags, source.list_name,
               source.subfolder, source.transcript, source.embedding
          from items source
         where source.group_id = %s
           and source.id = any(%s::uuid[])
           and not exists (
             select 1
               from items target
              where target.group_id = %s
                and (
                  (source.place_id is not null and target.place_id = source.place_id)
                  or target.source_url = source.source_url
                )
           )
        on conflict (group_id, place_id) do nothing
        returning id
        """,
        (target_group_id, source_group_id, item_ids, target_group_id),
    ).fetchall()

    conn.execute(
        """
        insert into item_saves (item_id, member_id)
        select distinct target.id, %s::uuid
          from items source
          join items target
            on target.group_id = %s
           and (
             (source.place_id is not null and target.place_id = source.place_id)
             or target.source_url = source.source_url
           )
         where source.group_id = %s
           and source.id = any(%s::uuid[])
        on conflict do nothing
        """,
        (member_id, target_group_id, source_group_id, item_ids),
    )
    added = len(inserted)
    return added, max(0, found - added)


def ensure_test_group(conn: Any, group_id: str) -> None:
    conn.execute(
        """
        insert into groups (id, wa_chat_id, name)
        values (%s, %s, %s)
        on conflict (id) do update
            set name = coalesce(groups.name, excluded.name)
        """,
        (group_id, f"app:{group_id}", "iOS test group"),
    )
    conn.commit()


def upsert_app_member(conn: Any, group_id: str, user_name: str) -> dict[str, Any]:
    return get_or_create_member(
        conn,
        group_id,
        wa_user_id=user_name,
        display_name=user_name,
    )


def enqueue_ingest_job(conn: Any, *, group_id: str, url: str, user_name: str, request_id: str | None = None) -> str:
    if request_id:
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"request:{user_name}:{request_id}",))
        previous = conn.execute("select id::text as id, group_id, payload, type from jobs where chat_id = 'app' and sender_id = %s and request_id = %s", (user_name, request_id)).fetchone()
        if previous:
            if str(previous["group_id"]) != group_id or previous["payload"] != url or previous["type"] != "ingest":
                raise HTTPException(status_code=409, detail="This request ID was already used for different content")
            return previous["id"]
    # Stable request IDs handle unknown network outcomes; a brief debounce also
    # protects double taps from clients that did not send one.
    conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{group_id}:{user_name}:{url}",))
    existing = conn.execute(
        """select id::text as id from jobs where group_id = %s and sender_id = %s and type = 'ingest'
           and payload = %s and ((%s::text is not null and request_id = %s)
           or status in ('queued', 'processing') or (status = 'done' and created_at > now() - interval '10 seconds'))
           order by created_at desc limit 1""",
        (group_id, user_name, url, request_id, request_id),
    ).fetchone()
    if existing:
        conn.commit()
        return existing["id"]
    row = conn.execute(
        """insert into jobs (group_id, chat_id, sender_id, type, payload, status, request_id)
        values (%s, 'app', %s, 'ingest', %s, 'queued', %s) returning id::text as id""",
        (group_id, user_name, url, request_id),
    ).fetchone()
    conn.commit()
    return row["id"]


def enqueue_query_job(
    conn: Any,
    *,
    group_id: str,
    text: str,
    user_name: str,
    history: list[dict[str, str]] | None = None,
    request_id: str | None = None,
) -> str:
    import json

    payload = json.dumps({"text": text, "history": history or []}, ensure_ascii=False) if history else text
    if request_id:
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"request:{user_name}:{request_id}",))
        previous = conn.execute("select id, group_id, payload, type from jobs where chat_id = 'app' and sender_id = %s and request_id = %s", (user_name, request_id)).fetchone()
        if previous:
            if str(previous["group_id"]) != group_id or previous["payload"] != payload or previous["type"] != "query":
                raise HTTPException(status_code=409, detail="Request ID already used")
            return str(previous["id"])
    row = conn.execute(
        """
        insert into jobs (group_id, chat_id, sender_id, type, payload, status, request_id)
        values (%s, 'app', %s, 'query', %s, 'queued', %s)
        returning id
        """,
        (group_id, user_name, payload, request_id),
    ).fetchone()
    conn.commit()
    return str(row["id"])


def wait_for_job_reply(job_id: str, timeout_seconds: float = QUERY_WAIT_SECONDS) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    with connect() as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                "select status, reply from jobs where id = %s",
                (job_id,),
            ).fetchone()
            conn.commit()
            if row and row["status"] in ("error", "cancelled"):
                raise HTTPException(status_code=503, detail=row.get("reply") or "Could not answer that question. Try again.", headers={"X-ReelBot-Job-Terminal": "true"})
            if row and row["status"] == "done":
                reply = (row.get("reply") or "").strip()
                if reply:
                    return reply
                return None
            time.sleep(QUERY_POLL_SECONDS)
    return None


def decode_query_reply(reply: str) -> QueryResponse:
    """Query jobs from the app carry a JSON envelope {answer, sources}; older
    replies and error messages are plain text."""
    if reply.startswith("{"):
        try:
            import json

            parsed = json.loads(reply)
            if isinstance(parsed, dict) and parsed.get("answer"):
                return QueryResponse(
                    answer=str(parsed["answer"]),
                    sources=[
                        QuerySource(title=str(s.get("title") or "Source"), url=str(s["url"]))
                        for s in parsed.get("sources") or []
                        if isinstance(s, dict) and s.get("url")
                    ],
                )
        except Exception:
            LOG.warning("Could not decode query reply envelope; returning raw text")
    return QueryResponse(answer=reply)


def drain_queued_jobs(limit: int = 2) -> None:
    if not api_drain_enabled():
        return
    if not INGEST_DRAIN_LOCK.acquire(blocking=False):
        return

    try:
        from db import claim_next_job, mark_job_error, requeue_retryable_ingest_errors
        from worker import handle_job, job_error_reply

        with connect() as conn:
            requeue_retryable_ingest_errors(conn)
            for _ in range(max(1, limit)):
                job = claim_next_job(conn)
                if job is None:
                    return

                try:
                    handle_job(conn, job)
                    conn.commit()
                except Exception as exc:
                    LOG.exception("Background job %s failed", job.get("id"))
                    conn.rollback()
                    try:
                        mark_job_error(
                            conn,
                            job["id"],
                            job_error_reply(exc),
                        )
                        log_event(conn, job.get("group_id"), "error", f"{type(exc).__name__}: {exc}")
                        conn.commit()
                    except Exception:
                        LOG.exception("Could not record failure for background job %s", job.get("id"))
    finally:
        INGEST_DRAIN_LOCK.release()


def saved_items(conn: Any, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        with saved as (
            select i.id::text as id,
                   i.place_name,
                   i.category,
                   i.location_text,
                   i.list_name,
                   i.subfolder,
                   i.source_url,
                   'saved'::text as status,
                   null::text as message,
                   null::text as job_id,
                   i.lat,
                   i.lng,
                   i.price_tier,
                   coalesce(i.tags, array[]::text[]) as tags,
                   count(distinct s.member_id)::int as save_count,
                   i.created_at as sort_at
              from items i
              left join item_saves s on s.item_id = i.id
             where i.group_id = %s
             group by i.id
        ),
        latest_jobs as (
            select *
              from (
                select j.*,
                       row_number() over (
                         partition by j.payload
                         order by j.created_at desc
                       ) as rn
                  from jobs j
                 where j.group_id = %s
                   and j.type = 'ingest'
                   and j.created_at >= now() - interval '2 days'
              ) ranked_jobs
             where rn = 1 and status in ('queued', 'processing', 'error')
        ),
        recent_jobs as (
            select null::text as id,
                   null::text as place_name,
                   null::text as category,
                   null::text as location_text,
                   null::text as list_name,
                   null::text as subfolder,
                   j.payload as source_url,
                   case
                     when j.status in ('queued', 'processing') then 'processing'
                     when j.status = 'error' then 'error'
                     else 'done'
                   end as status,
                   nullif(j.reply, '') as message,
                   j.id::text as job_id,
                   null::double precision as lat,
                   null::double precision as lng,
                   null::text as price_tier,
                   array[]::text[] as tags,
                   1::int as save_count,
                   j.created_at as sort_at
              from latest_jobs j
             where not exists (
               select 1
                 from items i
                where i.group_id = j.group_id
                  and (i.source_url = j.payload or i.id = j.item_id)
             )
        )
        select id, place_name, category, location_text, list_name, subfolder, source_url, status, message,
               lat, lng, price_tier, tags, save_count, job_id, sort_at::text as created_at
          from (
            select * from saved
            union all
            select * from recent_jobs
          ) combined
         order by sort_at desc, id nulls last, job_id nulls last
        """,
        (group_id, group_id),
    ).fetchall()
    return list(rows)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/groups", response_model=GroupResponse, dependencies=[Depends(require_api_key)])
def create_group(body: GroupCreateRequest, authenticated: str = Depends(require_device)) -> GroupResponse:
    device_id = bound_device(body.device_id, authenticated)
    with connect() as conn:
        code = generate_join_code(conn)
        row = conn.execute(
            """
            insert into groups (wa_chat_id, name, join_code)
            values (%s, %s, %s)
            returning id::text as id
            """,
            (f"appgroup:{uuid4()}", body.name, code),
        ).fetchone()
        get_or_create_member(conn, row["id"], wa_user_id=device_id, display_name=body.user_name)
        log_event(conn, row["id"], "group_create", body.name)
        groups = group_response_rows(conn, [row["id"]])
    return GroupResponse(**groups[0])


@app.post("/groups/join", response_model=GroupResponse, dependencies=[Depends(require_api_key)])
def join_group(body: GroupJoinRequest, authenticated: str = Depends(require_device)) -> GroupResponse:
    device_id = bound_device(body.device_id, authenticated)
    with connect() as conn:
        conn.execute("delete from app_join_attempts where attempted_at < now() - interval '10 minutes'")
        conn.execute("select id from app_devices where id = %s for update", (device_id,))
        attempts = conn.execute("select count(*) as n from app_join_attempts where device_id = %s", (device_id,)).fetchone()["n"]
        if attempts >= 10:
            raise HTTPException(status_code=429, detail="Too many invite attempts. Try again in 10 minutes.")
        conn.execute("insert into app_join_attempts(device_id) values (%s)", (device_id,))
        conn.commit()
    with connect() as conn:
        row = conn.execute(
            "select id::text as id from groups where join_code = %s",
            (body.code,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No group with that code")
        get_or_create_member(conn, row["id"], wa_user_id=device_id, display_name=body.user_name)
        log_event(conn, row["id"], "group_join", body.user_name)
        groups = group_response_rows(conn, [row["id"]])
    return GroupResponse(**groups[0])


@app.get("/groups", response_model=list[GroupResponse], dependencies=[Depends(require_api_key)])
def list_my_groups(
    device_id: str | None = None,
    user_name: str = "Friend",
    app_settings: Settings = Depends(settings),
    authenticated: str = Depends(require_device),
) -> list[GroupResponse]:
    device = bound_device(device_id, authenticated)
    if device is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id is required")
    with connect() as conn:
        # Everyone belongs to the shared default group so existing saves stay visible.
        ensure_test_group(conn, app_settings.test_group_id)
        get_or_create_member(conn, app_settings.test_group_id, wa_user_id=device, display_name=user_name)
        rows = conn.execute(
            "select group_id::text as group_id from members where wa_user_id = %s",
            (device,),
        ).fetchall()
        groups = group_response_rows(conn, [row["group_id"] for row in rows])
    for group in groups:
        if group["id"].lower() == app_settings.test_group_id.lower() and group["name"] == "iOS test group":
            group["name"] = "Shared Saves"
    return [GroupResponse(**group) for group in groups]


@app.post(
    "/groups/{group_id}/items",
    response_model=GroupItemsAddResponse,
    dependencies=[Depends(require_api_key)],
)
def add_existing_items_to_group(
    group_id: str,
    body: GroupItemsAddRequest,
    app_settings: Settings = Depends(settings),
    authenticated: str = Depends(require_device),
) -> GroupItemsAddResponse:
    device_id = bound_device(body.device_id, authenticated)
    with connect() as conn:
        target_group_id = resolve_group_id(
            conn,
            app_settings,
            group_id=group_id,
            device_id=device_id,
            user_name=body.user_name,
        )
        source_group_id = resolve_group_id(
            conn,
            app_settings,
            group_id=body.source_group_id,
            device_id=device_id,
            user_name=body.user_name,
        )
        if target_group_id == source_group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Choose a different source and destination group",
            )
        member = conn.execute(
            "select id::text as id from members where group_id = %s and wa_user_id = %s",
            (target_group_id, device_id),
        ).fetchone()
        if member is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this group")
        added, already_present = copy_items_between_groups(
            conn,
            source_group_id=source_group_id,
            target_group_id=target_group_id,
            item_ids=body.item_ids,
            member_id=member["id"],
        )
        log_event(
            conn,
            target_group_id,
            "group_items_add",
            f"{body.user_name}:{added} added:{already_present} already present",
        )
    return GroupItemsAddResponse(added=added, already_present=already_present)


@app.post(
    "/share",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ShareResponse,
    dependencies=[Depends(require_api_key)],
)
def share_reel(
    body: ShareRequest,
    background_tasks: BackgroundTasks,
    app_settings: Settings = Depends(settings),
    authenticated: str = Depends(require_device),
) -> ShareResponse:
    device_id = bound_device(body.device_id, authenticated)
    with connect() as conn:
        group_id = resolve_group_id(
            conn,
            app_settings,
            group_id=body.group_id,
            device_id=device_id,
            user_name=body.user_name,
        )
        job_id = enqueue_ingest_job(
            conn,
            group_id=group_id,
            url=body.url,
            user_name=device_id,
            request_id=body.request_id,
        )
    background_tasks.add_task(drain_queued_jobs)
    return ShareResponse(status="queued", job_id=job_id)


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
def query_saved_places(body: QueryRequest, app_settings: Settings = Depends(settings), authenticated: str = Depends(require_device)) -> QueryResponse:
    history = [turn.model_dump() for turn in body.history]
    device_id = bound_device(body.device_id, authenticated)
    with connect() as conn:
        group_id = resolve_group_id(
            conn,
            app_settings,
            group_id=body.group_id,
            device_id=device_id,
            user_name=body.user_name,
        )

    if api_drain_enabled():
        # Single-process mode: answer in this process (loads the ML stack).
        from retrieval import answer_question_structured

        structured = answer_question_structured(group_id, body.text, history=history)
        with connect() as conn:
            log_event(conn, group_id, "query", f"app:{body.user_name}:{body.text[:160]}")
        return QueryResponse(**structured)

    # Production mode: the dedicated worker owns the ML stack; hand it the
    # question as a job and wait for the reply (the worker logs the event).
    with connect() as conn:
        job_id = enqueue_query_job(
            conn,
            group_id=group_id,
            text=body.text,
            user_name=device_id,
            history=history,
            request_id=body.request_id,
        )
    reply = wait_for_job_reply(job_id)
    if reply is None:
        return QueryResponse(status="processing", job_id=job_id)
    return decode_query_reply(reply)


@app.delete("/items/{item_id}", dependencies=[Depends(require_api_key)])
def delete_item(
    item_id: str,
    group_id: str | None = None,
    device_id: str | None = None,
    user_name: str = "Friend",
    app_settings: Settings = Depends(settings),
    authenticated: str = Depends(require_device),
) -> dict[str, str]:
    try:
        UUID(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid item id") from exc

    with connect() as conn:
        scope = resolve_group_id(
            conn,
            app_settings,
            group_id=group_id,
            device_id=bound_device(device_id, authenticated),
            user_name=user_name,
        )
        conn.execute("select id from groups where id = %s for update", (scope,))
        conn.execute(
            """update jobs set status = 'cancelled', reply = 'Removed from this group', updated_at = now()
               where group_id = %s and type = 'ingest' and (item_id = %s or payload in
               (select source_url from items where id = %s and group_id = %s))""",
            (scope, item_id, item_id, scope),
        )
        conn.execute(
            """
            delete from item_saves
             where item_id in (select id from items where id = %s and group_id = %s)
            """,
            (item_id, scope),
        )
        deleted = conn.execute(
            "delete from items where id = %s and group_id = %s returning id",
            (item_id, scope),
        ).fetchone()
        conn.commit()

    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    with connect() as conn:
        log_event(conn, scope, "delete", item_id)
    return {"status": "deleted"}


@app.get("/items", response_model=list[ItemResponse], dependencies=[Depends(require_api_key)])
def list_items(
    background_tasks: BackgroundTasks,
    group_id: str | None = None,
    device_id: str | None = None,
    user_name: str = "Friend",
    app_settings: Settings = Depends(settings),
    authenticated: str = Depends(require_device),
) -> list[ItemResponse]:
    with connect() as conn:
        scope = resolve_group_id(
            conn,
            app_settings,
            group_id=group_id,
            device_id=bound_device(device_id, authenticated),
            user_name=user_name,
        )
        items = [ItemResponse(**row) for row in saved_items(conn, scope)]
    background_tasks.add_task(drain_queued_jobs)
    return items


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, app_settings: Settings = Depends(settings), authenticated: str = Depends(require_device)) -> JobResponse:
    with connect() as conn:
        row = conn.execute("select * from jobs where id = %s and chat_id = 'app' and sender_id = %s", (job_id, authenticated)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Import or question not found")
        resolve_group_id(conn, app_settings, group_id=str(row["group_id"]), device_id=authenticated, user_name="Friend")
    payload = JobResponse(id=str(row["id"]), status=row["status"], message=row.get("reply"), item_id=str(row["item_id"]) if row.get("item_id") else None)
    if row["type"] == "query" and row["status"] == "done" and row.get("reply"):
        decoded = decode_query_reply(row["reply"])
        payload.answer, payload.sources = decoded.answer, decoded.sources
        payload.message = None
    return payload
