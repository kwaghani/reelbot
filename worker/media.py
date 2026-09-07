from __future__ import annotations

import base64
import difflib
import hashlib
import html
import io
import ipaddress
import socket
import json
import logging
import os
import re
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from html.parser import HTMLParser
from urllib.request import Request, build_opener, HTTPRedirectHandler

from worker.content_quality import sanitize_transcript
from worker.reel_urls import canonical_reel_url

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")

try:
    import pytesseract
    import yt_dlp
    from PIL import Image, ImageOps
except ImportError as exc:
    raise SystemExit(
        f"Missing dependency: {exc}. Run `pip install -r worker/requirements.txt` in your venv."
    ) from exc


TRANSCRIPT_SAVE_CHARS = 1500
OCR_SAVE_CHARS = 800
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"

LOG = logging.getLogger("reelbot.pipeline")

_WHISPER_MODEL: Any | None = None


class StageError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


@dataclass
class IngestResult:
    reel_id: str
    workdir: Path
    video_path: Path | None
    thumbnail_path: Path | None
    info: dict[str, Any]
    caption: str
    metadata_only: bool
    audio_path: Path | None = None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def is_instagram_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return host in {"instagram.com", "www.instagram.com", "m.instagram.com"}


def is_probable_video_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if not path.name.startswith("source."):
        return False
    if path.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv"}:
        return False
    return True


def find_video_file(workdir: Path) -> Path | None:
    if not workdir.exists():
        return None
    candidates = sorted(
        (path for path in workdir.iterdir() if is_probable_video_file(path)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def extract_caption(info: dict[str, Any]) -> str:
    description = str(info.get("description") or "").strip()
    title = str(info.get("title") or "").strip()
    if description and title and title not in description:
        return f"{title}\n{description}"
    return description or title


def impersonate_target() -> Any | None:
    """TikTok's CDN 403s plain requests; impersonating a browser TLS
    fingerprint (needs curl_cffi) makes video downloads work."""
    try:
        import curl_cffi  # noqa: F401

        from yt_dlp.networking.impersonate import ImpersonateTarget

        return ImpersonateTarget.from_str("chrome")
    except Exception:
        return None


def ytdlp_options(url: str, workdir: Path | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "ignoreerrors": False,
        "ignore_no_formats_error": True,
        "socket_timeout": 20,
        "retries": 2,
        "max_filesize": 100_000_000,
        "fragment_retries": 2,
    }
    target = impersonate_target()
    if target is not None:
        opts["impersonate"] = target
    if workdir is not None:
        opts.update(
            {
                "outtmpl": str(workdir / "source.%(ext)s"),
                "format": "bv*+ba/best",
                "merge_output_format": "mp4",
            }
        )
    cookies_source = os.getenv("IG_COOKIES_PATH", "").strip()
    if cookies_source and is_instagram_url(url):
        browser = cookies_from_browser(cookies_source)
        if browser:
            opts["cookiesfrombrowser"] = browser
        else:
            opts["cookiefile"] = cookies_source
    return opts


def cookies_from_browser(cookies_source: str) -> tuple[str, ...] | None:
    source = cookies_source.strip().strip('"').strip("'")
    lowered = source.lower()
    if lowered in {"chrome", "browser:chrome", "from-browser:chrome"}:
        return ("chrome",)
    if lowered.startswith("browser:"):
        parts = tuple(part for part in source.split(":")[1:] if part)
        return parts or None
    return None


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def metadata_only_ingest_enabled() -> bool:
    value = os.getenv("REELBOT_ALLOW_METADATA_ONLY_INGEST", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def video_download_enabled() -> bool:
    value = os.getenv("REELBOT_ENABLE_VIDEO_DOWNLOAD", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }



def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid public URL")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError("Source must use a public internet address")


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_urlopen(request: Request, timeout: int = 20):
    validate_public_url(request.full_url)
    return build_opener(PublicRedirects()).open(request, timeout=timeout)


def public_page_text(url: str) -> tuple[str, str]:
    request = Request(url, headers=request_headers())
    with public_urlopen(request, timeout=20) as response:
        body = response.read(1_500_000)
        final_url = response.geturl()
        content_type = response.headers.get_content_charset() or "utf-8"
    return final_url, body.decode(content_type, errors="replace")


def html_tag_text(document: str, tag_name: str) -> str | None:
    match = re.search(rf"<{tag_name}[^>]*>(.*?)</{tag_name}>", document, flags=re.I | re.S)
    if not match:
        return None
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() or None


def html_meta_content(document: str, name: str) -> str | None:
    class MetaParser(HTMLParser):
        value = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag.lower() == "meta" and (attributes.get("name") or attributes.get("property") or "").lower() == name.lower():
                self.value = attributes.get("content")

    parser = MetaParser(convert_charrefs=True)
    parser.feed(document)
    return (parser.value or "").strip() or None


def public_metadata_info(url: str, workdir: Path) -> dict[str, Any]:
    try:
        final_url, document = public_page_text(url)
    except Exception as exc:
        raise StageError("ingest", f"public page metadata fetch failed: {exc}") from exc

    title = (
        html_meta_content(document, "og:title")
        or html_meta_content(document, "twitter:title")
        or html_tag_text(document, "title")
        or ""
    )
    description = (
        html_meta_content(document, "og:description")
        or html_meta_content(document, "description")
        or html_meta_content(document, "twitter:description")
        or ""
    )
    thumbnail = html_meta_content(document, "og:image") or html_meta_content(document, "twitter:image")

    if not title and not description and not thumbnail:
        raise StageError("ingest", "public page metadata did not include caption or thumbnail data")

    # Login/challenge/not-found pages also have titles and logos. They are
    # failure states, never evidence for a saved reel.
    try:
        final_url = canonical_reel_url(final_url)
    except ValueError as exc:
        raise StageError("ingest", "Source redirected away from the reel") from exc
    combined = (title + " " + description).lower()
    generic = {"instagram", "tiktok", "youtube", "instagram - log in", "tiktok - make your day", "youtube shorts"}
    blocked = ("log in to instagram", "login • instagram", "page isn't available", "page not found", "video unavailable", "this account is private", "this video is private", "verify to continue", "access denied", "sign in to confirm")
    if title.lower().strip() in generic and not description or any(phrase in combined for phrase in blocked):
        raise StageError("ingest", "This reel is unavailable or requires access on the original platform")
    if not description and not title:
        raise StageError("ingest", "No readable reel metadata was available")

    info = {
        "id": f"url_{url_hash(final_url)}",
        "webpage_url": final_url,
        "title": title,
        "description": description,
        "thumbnail": thumbnail,
        "extractor": "public_metadata",
    }
    write_json(workdir / "public_metadata.json", info)
    return info


def thumbnail_url(info: dict[str, Any]) -> str | None:
    direct = info.get("thumbnail")
    if isinstance(direct, str) and direct.startswith(("http://", "https://")):
        return direct

    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for entry in reversed(thumbnails):
            if isinstance(entry, dict):
                url = entry.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
    return None


def download_thumbnail(info: dict[str, Any], workdir: Path) -> Path | None:
    url = thumbnail_url(info)
    if not url:
        return None

    image_path = workdir / "thumbnail.jpg"
    try:
        request = Request(url, headers=request_headers())
        with public_urlopen(request, timeout=20) as response:
            image_path.write_bytes(response.read(5_000_000))
        if image_path.stat().st_size <= 512:
            image_path.unlink(missing_ok=True)
            return None
        return image_path
    except Exception as exc:
        LOG.warning("Thumbnail download failed: %s", exc)
        image_path.unlink(missing_ok=True)
        return None


def ingest_hint(url: str, message: str) -> str | None:
    lowered = message.lower()
    if is_instagram_url(url) and "empty media response" in lowered:
        cookies_source = os.getenv("IG_COOKIES_PATH", "").strip()
        if cookies_from_browser(cookies_source):
            return "Instagram still returned no media with browser cookies. Make sure you are logged into Instagram in that browser, then retry."
        if cookies_source:
            return "Instagram still returned no media. Check that IG_COOKIES_PATH points to a fresh Netscape-format cookies.txt export for instagram.com."
        return "Instagram requires auth for this URL. Export browser cookies for instagram.com and set IG_COOKIES_PATH=/absolute/path/to/cookies.txt in .env."
    if "unsupported url" in lowered and "tiktok.com" in lowered and "/photo/" in lowered:
        return "This TikTok URL is a photo post, not a video/reel URL. This spike expects downloadable video."
    return None


def ytdlp_extract(url: str, *, download: bool, workdir: Path | None = None) -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(ytdlp_options(url, workdir)) as ydl:
            info = ydl.extract_info(url, download=download)
    except Exception as exc:
        action = "download" if download else "metadata"
        message = strip_ansi(str(exc))
        hint = ingest_hint(url, message)
        if hint:
            message = f"{message} Hint: {hint}"
        raise StageError("ingest", f"yt-dlp {action} failed: {message}") from exc

    if not isinstance(info, dict):
        raise StageError("ingest", "yt-dlp returned no info object")
    return info


def has_downloadable_video(info: dict[str, Any]) -> bool:
    if info.get("url"):
        return True
    formats = info.get("formats")
    return isinstance(formats, list) and any(fmt.get("url") for fmt in formats if isinstance(fmt, dict))


def select_video_info(info: dict[str, Any]) -> dict[str, Any]:
    if has_downloadable_video(info):
        return info

    entries = info.get("entries") or []
    for entry in entries:
        if isinstance(entry, dict) and has_downloadable_video(entry):
            return entry

    raise StageError(
        "ingest",
        "yt-dlp found metadata but no downloadable video formats. "
        "For Instagram, this usually means the URL is an image-only post rather than a reel/video.",
    )


def download_video_info(video_info: dict[str, Any], url: str, workdir: Path) -> None:
    try:
        with yt_dlp.YoutubeDL(ytdlp_options(url, workdir)) as ydl:
            ydl.process_info(video_info)
    except Exception as exc:
        message = strip_ansi(str(exc))
        hint = ingest_hint(url, message)
        if hint:
            message = f"{message} Hint: {hint}"
        raise StageError("ingest", f"yt-dlp selected media download failed: {message}") from exc


def stage_ingest(url: str, workdir: Path) -> IngestResult:
    url = canonical_reel_url(url)
    workdir.mkdir(parents=True, exist_ok=True)
    metadata_only = not video_download_enabled()
    if metadata_only and is_instagram_url(url):
        metadata = public_metadata_info(url, workdir)
    else:
        try:
            metadata = ytdlp_extract(url, download=False)
        except StageError as exc:
            if not metadata_only_ingest_enabled():
                raise
            LOG.warning("yt-dlp metadata failed; falling back to public page metadata: %s", exc.message)
            metadata = public_metadata_info(url, workdir)
            metadata_only = True

    if metadata.get("availability") in {"private", "needs_auth", "subscriber_only", "premium_only"}:
        raise StageError("ingest", "This reel is restricted on the original platform")
    title = str(metadata.get("title") or "").strip()
    if re.fullmatch(r"youtube video #[A-Za-z0-9_-]+", title, re.I):
        raise StageError("ingest", "This YouTube video is unavailable")
    reel_id = str(metadata.get("id") or f"url_{url_hash(url)}")
    write_json(workdir / "info.json", metadata)

    selected_video_info: dict[str, Any] | None = None
    if video_download_enabled() and not metadata_only:
        try:
            selected_video_info = select_video_info(metadata)
        except StageError as exc:
            if not metadata_only_ingest_enabled():
                raise
            LOG.warning("No downloadable video found; continuing with metadata only: %s", exc.message)
            metadata_only = True
        if selected_video_info is not None and selected_video_info is not metadata:
            write_json(workdir / "selected_video_info.json", selected_video_info)
            LOG.info(
                "[%s] selected child video %s",
                reel_id,
                selected_video_info.get("id") or "unknown",
            )

    video_path = find_video_file(workdir)
    if video_path:
        LOG.info("[%s] video cache hit", reel_id)
    elif selected_video_info is not None:
        LOG.info("[%s] downloading source video", reel_id)
        try:
            # Extract and download in one yt-dlp session: TikTok media URLs
            # are bound to cookies set during extraction, so a fresh session
            # replaying previously-extracted format URLs gets 403s.
            ytdlp_extract(url, download=True, workdir=workdir)
            video_path = find_video_file(workdir)
            if video_path is None:
                raise StageError("ingest", "yt-dlp completed but no source video was found")
        except StageError as exc:
            if not metadata_only_ingest_enabled():
                raise
            LOG.warning("Video download failed; continuing with metadata only: %s", exc.message)
            metadata_only = True

    caption = extract_caption(metadata)
    try:
        thumbnail_path = download_thumbnail(selected_video_info or metadata, workdir)
    except Exception as exc:
        LOG.warning("Thumbnail unavailable: %s", type(exc).__name__)
        thumbnail_path = None

    audio_path = None
    if video_path is None and video_download_enabled() and transcription_enabled():
        audio_path = download_audio_only(url, workdir)

    if video_path is None and audio_path is None and not caption and thumbnail_path is None:
        raise StageError("ingest", "metadata-only fallback had no caption or thumbnail to inspect")

    (workdir / "caption.txt").write_text(caption, encoding="utf-8")
    return IngestResult(
        reel_id=reel_id,
        workdir=workdir,
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        info=metadata,
        caption=caption,
        metadata_only=metadata_only or video_path is None,
        audio_path=audio_path,
    )


def looks_like_no_audio(stderr: str) -> bool:
    lowered = stderr.lower()
    markers = [
        "does not contain any stream",
        "matches no streams",
        "stream map '0:a",
        "stream specifier",
        "no such stream",
    ]
    return any(marker in lowered for marker in markers)


def wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            if rate <= 0:
                return 0.0
            return frames / float(rate)
    except Exception:
        return 0.0


def extract_audio(video_path: Path, workdir: Path) -> Path | None:
    audio_path = workdir / "audio.wav"
    if audio_path.exists() and audio_path.stat().st_size > 1024:
        return audio_path

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=90)
    except Exception as exc:
        raise StageError("audio", f"ffmpeg audio extraction failed: {exc}") from exc

    if proc.returncode != 0:
        if looks_like_no_audio(proc.stderr):
            LOG.info("No audio stream found in %s", video_path)
            return None
        raise StageError("audio", f"ffmpeg audio extraction failed: {proc.stderr.strip()}")

    if not audio_path.exists() or audio_path.stat().st_size <= 1024:
        return None
    if wav_duration_seconds(audio_path) < 0.1:
        return None
    return audio_path


def transcription_enabled() -> bool:
    value = os.getenv("REELBOT_ENABLE_TRANSCRIPTION", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def download_audio_only(url: str, workdir: Path) -> Path | None:
    """Fetch just the audio track for transcription; cheap compared to full video."""
    existing = sorted(workdir.glob("audiosrc.*"))
    if existing:
        return existing[0]

    opts = ytdlp_options(url)
    opts.update(
        {
            "outtmpl": str(workdir / "audiosrc.%(ext)s"),
            "format": "ba/bestaudio/best",
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        LOG.warning("Audio-only download failed; continuing without transcript: %s", strip_ansi(str(exc)))
        return None

    candidates = sorted(workdir.glob("audiosrc.*"))
    for candidate in candidates:
        if candidate.stat().st_size > 1024:
            return candidate
    return None


def get_whisper_model() -> Any:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL

    try:
        from faster_whisper import WhisperModel

        _WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    except Exception as exc:
        raise StageError("audio", f"faster-whisper model load failed: {exc}") from exc
    return _WHISPER_MODEL


def stage_transcript(video_path: Path, workdir: Path) -> str:
    if not transcription_enabled():
        transcript = ""
        (workdir / "transcript.txt").write_text(transcript, encoding="utf-8")
        return transcript

    audio_path = extract_audio(video_path, workdir)
    if audio_path is None:
        transcript = ""
        (workdir / "transcript.txt").write_text(transcript, encoding="utf-8")
        return transcript

    try:
        model = get_whisper_model()
        segments, _info = model.transcribe(str(audio_path))
        # Preserve Whisper's segment boundaries so downstream quality filters
        # can remove noisy music without throwing away nearby useful speech.
        transcript = "\n".join(segment.text.strip() for segment in segments if segment.text).strip()
    except StageError:
        raise
    except Exception as exc:
        raise StageError("audio", f"faster-whisper transcription failed: {exc}") from exc

    (workdir / "transcript.txt").write_text(transcript, encoding="utf-8")
    return transcript


def sample_frames(video_path: Path, workdir: Path) -> list[Path]:
    frames_dir = workdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                           capture_output=True, text=True, timeout=8, check=True)
    duration = max(1.0, float(probe.stdout.strip()))
    frames = []
    for index in range(5):
        target = frames_dir / f"frame_{index}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(duration*(index+0.5)/5),
                        "-i", str(video_path), "-frames:v", "1", "-vf", "scale=960:-2", str(target)],
                       capture_output=True, timeout=8, check=True)
        if target.exists():
            frames.append(target)
    return frames


def clean_ocr_text(raw_texts: list[str]) -> str:
    seen: set[str] = set()
    cleaned: list[str] = []

    for raw_text in raw_texts:
        for raw_line in raw_text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            line = line.strip("|[]{}<>_~`'\"")
            if len(line) < 2:
                continue
            alnum_count = sum(ch.isalnum() for ch in line)
            if alnum_count < 2:
                continue
            if alnum_count / max(len(line), 1) < 0.35:
                continue
            key = line.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(line)

    return "\n".join(cleaned)


def stage_ocr(video_path: Path, workdir: Path) -> str:
    frames = sample_frames(video_path, workdir)
    if not frames:
        ocr_text = ""
        (workdir / "ocr.txt").write_text(ocr_text, encoding="utf-8")
        return ocr_text

    raw_texts: list[str] = []
    failures = 0
    for frame_path in frames:
        try:
            with Image.open(frame_path) as image:
                raw_texts.append(pytesseract.image_to_string(image))
        except pytesseract.TesseractNotFoundError as exc:
            raise StageError("ocr", f"tesseract executable not found: {exc}") from exc
        except Exception as exc:
            failures += 1
            LOG.warning("OCR failed for frame %s: %s", frame_path, exc)

    if failures == len(frames):
        raise StageError("ocr", "OCR failed for every sampled frame")

    ocr_text = clean_ocr_text(raw_texts)
    (workdir / "ocr.txt").write_text(ocr_text, encoding="utf-8")
    return ocr_text


def stage_thumbnail_ocr(image_path: Path | None, workdir: Path) -> str:
    if image_path is None:
        ocr_text = ""
        (workdir / "ocr.txt").write_text(ocr_text, encoding="utf-8")
        return ocr_text

    try:
        with Image.open(image_path) as image:
            raw_text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise StageError("ocr", f"tesseract executable not found: {exc}") from exc
    except Exception as exc:
        LOG.warning("Thumbnail OCR failed for %s: %s", image_path, exc)
        raw_text = ""

    ocr_text = clean_ocr_text([raw_text])
    (workdir / "ocr.txt").write_text(ocr_text, encoding="utf-8")
    return ocr_text


