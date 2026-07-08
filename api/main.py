from __future__ import annotations

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
SUPPORTED_REEL_URL = re.compile(
    r"^https?://(?:[\w-]+\.)?(?:instagram\.com|tiktok\.com|youtube\.com|youtu\.be)/[^\s<>\")']+$",
    re.IGNORECASE,
)


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
        return value


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

    @field_validator("url")
    @classmethod
    def clean_supported_url(cls, value: str) -> str:
        value = value.strip()
        if not SUPPORTED_REEL_URL.match(value):
            raise ValueError("url must be an Instagram or TikTok URL")
        return value

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
        if not value:
            raise ValueError("code is required")
        return value


class GroupResponse(BaseModel):
    id: str
    name: str
    join_code: str | None
    member_count: int = 0
    item_count: int = 0


class ShareResponse(BaseModel):
    status: str


class QuerySource(BaseModel):
    title: str
    url: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[QuerySource] = []


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
        LOG.error("API settings are invalid: %s", exc)
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
    LOG.info("Invalid request body: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request body"},
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
    if not group_id or group_id == app_settings.test_group_id:
        ensure_test_group(conn, app_settings.test_group_id)
        get_or_create_member(
            conn,
            app_settings.test_group_id,
            wa_user_id=device_id or user_name,
            display_name=user_name,
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


def enqueue_ingest_job(conn: Any, *, group_id: str, url: str, user_name: str) -> None:
    conn.execute(
        """
        insert into jobs (group_id, chat_id, sender_id, type, payload, status)
        values (%s, 'app', %s, 'ingest', %s, 'queued')
        """,
        (group_id, user_name, url),
    )
    conn.commit()


def enqueue_query_job(
    conn: Any,
    *,
    group_id: str,
    text: str,
    user_name: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    import json

    payload = json.dumps({"text": text, "history": history or []}, ensure_ascii=False) if history else text
    row = conn.execute(
        """
        insert into jobs (group_id, chat_id, sender_id, type, payload, status)
        values (%s, 'app', %s, 'query', %s, 'queued')
        returning id
        """,
        (group_id, user_name, payload),
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
            if row and row["status"] in ("done", "error"):
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
                   and j.status in ('queued', 'processing', 'error')
                   and j.created_at >= now() - interval '2 days'
              ) ranked_jobs
             where rn = 1
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
                  and i.source_url = j.payload
             )
        )
        select id, place_name, category, location_text, list_name, subfolder, source_url, status, message,
               lat, lng, price_tier, tags, save_count
          from (
            select * from saved
            union all
            select * from recent_jobs
          ) combined
         order by sort_at desc
        """,
        (group_id, group_id),
    ).fetchall()
    return list(rows)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/groups", response_model=GroupResponse, dependencies=[Depends(require_api_key)])
def create_group(body: GroupCreateRequest) -> GroupResponse:
    device_id = clean_device_id(body.device_id)
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
        conn.commit()
        get_or_create_member(conn, row["id"], wa_user_id=device_id, display_name=body.user_name)
        log_event(conn, row["id"], "group_create", body.name)
        groups = group_response_rows(conn, [row["id"]])
    return GroupResponse(**groups[0])


@app.post("/groups/join", response_model=GroupResponse, dependencies=[Depends(require_api_key)])
def join_group(body: GroupJoinRequest) -> GroupResponse:
    device_id = clean_device_id(body.device_id)
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
    device_id: str,
    user_name: str = "Friend",
    app_settings: Settings = Depends(settings),
) -> list[GroupResponse]:
    device = clean_device_id(device_id)
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
    "/share",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ShareResponse,
    dependencies=[Depends(require_api_key)],
)
def share_reel(
    body: ShareRequest,
    background_tasks: BackgroundTasks,
    app_settings: Settings = Depends(settings),
) -> ShareResponse:
    device_id = clean_device_id(body.device_id)
    with connect() as conn:
        group_id = resolve_group_id(
            conn,
            app_settings,
            group_id=body.group_id,
            device_id=device_id,
            user_name=body.user_name,
        )
        enqueue_ingest_job(
            conn,
            group_id=group_id,
            url=body.url,
            user_name=body.user_name,
        )
    background_tasks.add_task(drain_queued_jobs)
    return ShareResponse(status="queued")


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
def query_saved_places(body: QueryRequest, app_settings: Settings = Depends(settings)) -> QueryResponse:
    with connect() as conn:
        ensure_test_group(conn, app_settings.test_group_id)
        upsert_app_member(conn, app_settings.test_group_id, body.user_name)

    history = [turn.model_dump() for turn in body.history]
    device_id = clean_device_id(body.device_id)
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
            user_name=body.user_name,
            history=history,
        )
    reply = wait_for_job_reply(job_id)
    if reply is None:
        return QueryResponse(answer="Still working on that one — ask me again in a few seconds.")
    return decode_query_reply(reply)


@app.delete("/items/{item_id}", dependencies=[Depends(require_api_key)])
def delete_item(
    item_id: str,
    group_id: str | None = None,
    device_id: str | None = None,
    user_name: str = "Friend",
    app_settings: Settings = Depends(settings),
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
            device_id=clean_device_id(device_id),
            user_name=user_name,
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
) -> list[ItemResponse]:
    with connect() as conn:
        scope = resolve_group_id(
            conn,
            app_settings,
            group_id=group_id,
            device_id=clean_device_id(device_id),
            user_name=user_name,
        )
        items = [ItemResponse(**row) for row in saved_items(conn, scope)]
    background_tasks.add_task(drain_queued_jobs)
    return items
