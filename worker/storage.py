"""The only object-storage boundary used by ReelBot."""
from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Protocol

from config import settings

LOG = logging.getLogger("reelbot.storage")


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_url(self, key: str) -> str: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    """Safe stand-in for local development and tests, never selected silently."""
    def __init__(self, root: Path | None = None):
        self.root = root or Path(tempfile.gettempdir()) / "reelbot-storage"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root.resolve() not in candidate.parents:
            raise ValueError("Object key escapes local storage")
        return candidate

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)

    def get_url(self, key: str) -> str:
        return self._path(key).as_uri()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class R2Storage:
    def __init__(self):
        # boto3 must remain private to this implementation.
        import boto3
        from botocore.config import Config
        current = settings()
        if not current.has_r2:
            raise RuntimeError("R2 configuration is incomplete")
        self.bucket = str(current.r2_bucket)
        self.client = boto3.client("s3", endpoint_url=f"https://{current.r2_account_id}.r2.cloudflarestorage.com",
            region_name="auto", aws_access_key_id=current.r2_access_key_id,
            aws_secret_access_key=current.r2_secret_access_key, config=Config(signature_version="s3v4"))

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_url(self, key: str) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            code = getattr(getattr(exc, 'response', {}), 'get', lambda *_: {})('Error', {}).get('Code')
            if str(code) in {'404', 'NoSuchKey', 'NotFound'}:
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_storage: Storage | None = None


def _backend() -> Storage:
    global _storage
    if _storage is None:
        if settings().has_r2:
            _storage = R2Storage()
        else:
            LOG.error("storage_degraded local_storage_active r2_credentials_absent=true")
            _storage = LocalStorage()
    return _storage


def put(key: str, data: bytes, content_type: str) -> None:
    global _storage
    try:
        _backend().put(key, data, content_type)
    except Exception as exc:
        # Development stays useful during an R2 outage, but production logs make
        # the degraded condition unmistakable instead of hiding lost media.
        LOG.error("storage_degraded r2_write_failed=true key=%s error=%s", key, type(exc).__name__)
        _storage = LocalStorage()
        _storage.put(key, data, content_type)


def get_url(key: str) -> str:
    return _backend().get_url(key)


def exists(key: str) -> bool:
    return _backend().exists(key)


def delete(key: str) -> None:
    # Privacy deletion must not mistake a degraded local fallback for R2 success.
    backend = _backend()
    if settings().has_r2 and not isinstance(backend, R2Storage):
        R2Storage().delete(key)
    backend.delete(key)
    if not isinstance(backend, LocalStorage): LocalStorage().delete(key)


def _thumbnail(data: bytes, entry_id: str) -> tuple[str, bytes, str]:
    """Create a card-size thumbnail and write it before returning its key."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((800, 1600), Image.Resampling.LANCZOS)
        for limit in ((800, 1600), (600, 1200), (480, 960), (360, 720), (280, 560), (220, 440)):
            candidate = image.copy(); candidate.thumbnail(limit, Image.Resampling.LANCZOS)
            for quality in (80, 72, 64, 56):
                output = io.BytesIO()
                try:
                    candidate.save(output, "WEBP", quality=quality, method=6)
                    suffix, content_type = "webp", "image/webp"
                except OSError:
                    output = io.BytesIO(); candidate.save(output, "JPEG", quality=quality, optimize=True)
                    suffix, content_type = "jpg", "image/jpeg"
                if output.tell() < 60_000:
                    key = f"thumbs/{entry_id}.{suffix}"
                    payload = output.getvalue(); put(key, payload, content_type)
                    LOG.info("thumbnail_written key=%s bytes=%d", key, len(payload))
                    return key, payload, content_type
    raise ValueError("Thumbnail exceeds 60 KB budget")
