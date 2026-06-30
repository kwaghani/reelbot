from __future__ import annotations

import logging
import os
import re
import secrets
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
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
SUPPORTED_REEL_URL = re.compile(
    r"^https?://(?:[\w-]+\.)?(?:instagram\.com|tiktok\.com)/[^\s<>\")']+$",
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


class ShareRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    user_name: str = Field(min_length=1, max_length=120)

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


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    user_name: str = Field(min_length=1, max_length=120)

    @field_validator("text", "user_name")
    @classmethod
    def clean_text_field(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("field is required")
        return value


class ShareResponse(BaseModel):
    status: str


class QueryResponse(BaseModel):
    answer: str


class ItemResponse(BaseModel):
    place_name: str | None
    category: str | None
    location_text: str | None
    list_name: str | None
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
    dependencies=[Depends(require_api_key)],
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


def saved_items(conn: Any, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select i.place_name,
               i.category,
               i.location_text,
               i.list_name,
               i.lat,
               i.lng,
               i.price_tier,
               coalesce(i.tags, array[]::text[]) as tags,
               count(distinct s.member_id) as save_count
          from items i
          left join item_saves s on s.item_id = i.id
         where i.group_id = %s
         group by i.id
         order by i.created_at desc
        """,
        (group_id,),
    ).fetchall()
    return list(rows)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/share", status_code=status.HTTP_202_ACCEPTED, response_model=ShareResponse)
def share_reel(body: ShareRequest, app_settings: Settings = Depends(settings)) -> ShareResponse:
    with connect() as conn:
        ensure_test_group(conn, app_settings.test_group_id)
        upsert_app_member(conn, app_settings.test_group_id, body.user_name)
        enqueue_ingest_job(
            conn,
            group_id=app_settings.test_group_id,
            url=body.url,
            user_name=body.user_name,
        )
    return ShareResponse(status="queued")


@app.post("/query", response_model=QueryResponse)
def query_saved_places(body: QueryRequest, app_settings: Settings = Depends(settings)) -> QueryResponse:
    from retrieval import answer_question

    with connect() as conn:
        ensure_test_group(conn, app_settings.test_group_id)
        upsert_app_member(conn, app_settings.test_group_id, body.user_name)

    answer = answer_question(app_settings.test_group_id, body.text)

    with connect() as conn:
        log_event(conn, app_settings.test_group_id, "query", f"app:{body.user_name}:{body.text[:160]}")

    return QueryResponse(answer=answer)


@app.get("/items", response_model=list[ItemResponse])
def list_items(app_settings: Settings = Depends(settings)) -> list[ItemResponse]:
    with connect() as conn:
        ensure_test_group(conn, app_settings.test_group_id)
        return [ItemResponse(**row) for row in saved_items(conn, app_settings.test_group_id)]
