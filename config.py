"""Runtime configuration for ReelBot services.

This is deliberately the only module that reads process environment variables.
Importing it is harmless; validation happens when a service explicitly starts.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


LOG = logging.getLogger("reelbot.config")
ROOT = Path(__file__).resolve().parent


class ConfigurationError(RuntimeError):
    """A required deployment variable was not supplied."""


def _value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _required(name: str) -> str:
    value = _value(name)
    if value is None:
        raise ConfigurationError(f"Required configuration variable {name} is missing")
    return value


def normalize_database_url(value: str) -> str:
    """Make Render's legacy postgres URL explicit about the SQLAlchemy driver."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg2://" + value[len("postgres://"):]
    return value


def psycopg_database_url(value: str) -> str:
    """Convert the normalized URL for this project's native psycopg pool."""
    return normalize_database_url(value).replace("postgresql+psycopg2://", "postgresql://", 1)


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str | None
    r2_account_id: str | None
    r2_access_key_id: str | None
    r2_secret_access_key: str | None
    r2_bucket: str | None
    google_maps_api_key: str | None
    anthropic_api_key: str | None
    service_role: str
    fetch_proxy: str | None
    cookie_file_path: str | None
    imagery_cache: Path
    content_types_path: Path
    tesseract_command: str
    apple_bundle_id: str
    anthropic_fast_model: str
    anthropic_vision_model: str
    embedding_model: str
    allow_metadata_only_ingest: bool
    enable_video_download: bool
    enable_transcription: bool
    geography_lookups: bool
    disable_rate_limit: bool
    instagram_oembed_token: str | None
    nominatim_url: str
    nominatim_user_agent: str
    llm_input_usd_per_million: float
    llm_output_usd_per_million: float
    places_search_usd_per_call: float
    coords_batch_size: int
    coords_max_places_per_run: int
    coords_retries: int
    coords_backoff_seconds: float
    coords_rate_seconds: float
    coords_usd_per_call: float

    @property
    def has_r2(self) -> bool:
        return all((self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key, self.r2_bucket))

    @property
    def pool_size(self) -> int:
        return 5 if self.service_role == "api" else 2

    @property
    def max_overflow(self) -> int:
        return 5 if self.service_role == "api" else 2

    @property
    def pool_ceiling(self) -> int:
        return self.pool_size + self.max_overflow


@lru_cache(maxsize=1)
def settings() -> RuntimeConfig:
    """Read every deployment value once; no secrets are logged or returned."""
    role = _value("REELBOT_SERVICE_ROLE", "api")
    if role not in {"api", "worker"}:
        raise ConfigurationError("REELBOT_SERVICE_ROLE must be api or worker")
    return RuntimeConfig(
        database_url=(_value("DATABASE_URL") or None),
        r2_account_id=_value("R2_ACCOUNT_ID"), r2_access_key_id=_value("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_value("R2_SECRET_ACCESS_KEY"), r2_bucket=_value("R2_BUCKET"),
        google_maps_api_key=_value("GOOGLE_MAPS_API_KEY"), anthropic_api_key=_value("ANTHROPIC_API_KEY"),
        service_role=role, fetch_proxy=_value("REELBOT_FETCH_PROXY"), cookie_file_path=_value("IG_COOKIES_PATH"),
        imagery_cache=Path(_value("REELBOT_IMAGERY_CACHE", "/tmp/reelbot-imagery") or "/tmp/reelbot-imagery"),
        content_types_path=Path(_value("CONTENT_TYPES_PATH", str(ROOT / "config/content_types.yaml")) or ROOT / "config/content_types.yaml"),
        tesseract_command=_value("TESSERACT_CMD", "tesseract") or "tesseract",
        apple_bundle_id=_value("APPLE_BUNDLE_ID", "com.krishwaghani.reelbot") or "com.krishwaghani.reelbot",
        anthropic_fast_model=_value("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20251001") or "claude-haiku-4-5-20251001",
        anthropic_vision_model=_value("ANTHROPIC_VISION_MODEL", _value("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20251001")) or "claude-haiku-4-5-20251001",
        embedding_model=_value("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5") or "BAAI/bge-small-en-v1.5",
        allow_metadata_only_ingest=_truthy(_value("REELBOT_ALLOW_METADATA_ONLY_INGEST"), default=True),
        enable_video_download=_truthy(_value("REELBOT_ENABLE_VIDEO_DOWNLOAD"), default=True),
        enable_transcription=_truthy(_value("REELBOT_ENABLE_TRANSCRIPTION"), default=True),
        geography_lookups=_truthy(_value("REELBOT_GEOGRAPHY_LOOKUPS"), default=True),
        disable_rate_limit=_truthy(_value("REELBOT_DISABLE_RATE_LIMIT"), default=False),
        instagram_oembed_token=_value("INSTAGRAM_OEMBED_TOKEN"),
        nominatim_url=_value("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search") or "https://nominatim.openstreetmap.org/search",
        nominatim_user_agent=_value("NOMINATIM_USER_AGENT", "ReelBot/1.0 personal saved-venue lookup") or "ReelBot/1.0 personal saved-venue lookup",
        llm_input_usd_per_million=float(_value("LLM_INPUT_USD_PER_MILLION", "1") or "1"),
        llm_output_usd_per_million=float(_value("LLM_OUTPUT_USD_PER_MILLION", "5") or "5"),
        places_search_usd_per_call=float(_value("PLACES_SEARCH_USD_PER_CALL", "0.032") or "0.032"),
        coords_batch_size=max(1,min(1000,int(_value('COORDS_BATCH_SIZE','100') or '100'))),
        coords_max_places_per_run=max(1,min(10000,int(_value('COORDS_MAX_PLACES_PER_RUN','2000') or '2000'))),
        coords_retries=max(1,min(3,int(_value('COORDS_RETRIES','3') or '3'))),
        coords_backoff_seconds=max(0,float(_value('COORDS_BACKOFF_SECONDS','1') or '1')),
        coords_rate_seconds=max(0.2,float(_value('COORDS_RATE_SECONDS','0.2') or '0.2')),
        coords_usd_per_call=float(_value('COORDS_USD_PER_CALL','0.005') or '0.005'),
    )


def validate_service_config() -> RuntimeConfig:
    """Fail a service boot on missing mandatory production configuration."""
    current = settings()
    missing = [name for name, value in (("DATABASE_URL", current.database_url),
                                        ("GOOGLE_MAPS_API_KEY", current.google_maps_api_key),
                                        ("ANTHROPIC_API_KEY", current.anthropic_api_key)) if not value]
    if missing:
        raise ConfigurationError("Required configuration variable " + ", ".join(missing) + " is missing")
    # R2 is intentionally optional for a local loopback database. Any deployed
    # service must have all four values and may not silently fall back locally.
    r2_values = (current.r2_account_id, current.r2_access_key_id, current.r2_secret_access_key, current.r2_bucket)
    database_host = (current.database_url or "").split("@")[-1].split("/")[0].split(":")[0]
    local_database = database_host in {"localhost", "127.0.0.1", "::1"}
    if (any(r2_values) and not all(r2_values)) or (not local_database and not all(r2_values)):
        raise ConfigurationError("R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET must be supplied together")
    LOG.info("runtime_configuration %s", {name: bool(value) for name, value in {
        "DATABASE_URL": current.database_url, "R2_ACCOUNT_ID": current.r2_account_id,
        "R2_ACCESS_KEY_ID": current.r2_access_key_id, "R2_SECRET_ACCESS_KEY": current.r2_secret_access_key,
        "R2_BUCKET": current.r2_bucket, "GOOGLE_MAPS_API_KEY": current.google_maps_api_key,
        "ANTHROPIC_API_KEY": current.anthropic_api_key, "REELBOT_FETCH_PROXY": current.fetch_proxy,
        "IG_COOKIES_PATH": current.cookie_file_path}.items()})
    return current


def reset_for_tests() -> None:
    """Tests that patch environment values can reload this immutable configuration."""
    settings.cache_clear()


def content_types_path() -> Path:
    """Development-only registry override; required service settings stay frozen."""
    return Path(_value("CONTENT_TYPES_PATH", str(ROOT / "config/content_types.yaml")) or ROOT / "config/content_types.yaml")
