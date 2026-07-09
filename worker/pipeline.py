from __future__ import annotations

import hashlib
import html
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
from urllib.request import Request, urlopen

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")

try:
    import anthropic
    import googlemaps
    import pytesseract
    import yt_dlp
    from PIL import Image
except ImportError as exc:
    raise SystemExit(
        f"Missing dependency: {exc}. Run `pip install -r worker/requirements.txt` in your venv."
    ) from exc


TRANSCRIPT_SAVE_CHARS = 1500
OCR_SAVE_CHARS = 800
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "You are extracting structured info from shared social content (reels/TikToks/Shorts) "
    "so it can be filed into the right folder; "
    "respond with ONLY a JSON object, no prose, no markdown fences."
)

SCHEMA_PROMPT = """
Return exactly one JSON object with this schema:
{
  "has_content": bool,
  "content_type": str|null,
  "title": str|null,
  "has_place": bool,
  "place_name": str|null,
  "location_text": str|null,
  "category": str|null,
  "price_tier": str|null,
  "tags": [str],
  "confidence": float
}

content_type is one lowercase word for what this content fundamentally is, e.g.:
"place", "recipe", "restaurant", "workout", "sports", "product", "travel",
"fashion", "beauty", "tech", "car", "music", "movie", "gaming", "pet", "home",
"book", "diy", "meme", "advice", "finance", "other".
Pick the type from the SUBJECT of the content, not its tone — a funny sports
clip is "sports", not "meme". Reserve "meme" for content whose main point is
just to be funny.
title is a short natural name for the saved item (e.g. the dish for a recipe,
the place name for a spot, the product name for a product, a short description
for a funny clip). Always set a title when has_content=true.
Use has_place=true ONLY when the content is about one identifiable real-world
place/venue that could be looked up on a map; then also set place_name.
category is a short human label used for grouping, e.g. "Recipe", "Restaurant",
"Workout", "Travel", "Gadget", "Meme".
Almost everything is savable: if you can tell what the content is about at all
(a funny clip, a couple moment, a fit check, a vibe), set has_content=true and
describe it. Use has_content=false ONLY when the inputs are empty or
unintelligible.
price_tier must be "$", "$$", "$$$", or null.
confidence is 0..1 and is your confidence in title/place_name.
""".strip()

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
    return "instagram.com" in host


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
        "retries": 3,
        "fragment_retries": 3,
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
    value = os.getenv("REELBOT_ENABLE_VIDEO_DOWNLOAD", "").strip().lower()
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


def public_page_text(url: str) -> tuple[str, str]:
    request = Request(url, headers=request_headers())
    with urlopen(request, timeout=20) as response:
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
    name_pattern = re.escape(name)
    patterns = [
        rf"<meta[^>]+(?:name|property)=[\"']{name_pattern}[\"'][^>]+content=[\"']([^\"']*)",
        rf"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]+(?:name|property)=[\"']{name_pattern}[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, document, flags=re.I | re.S)
        if match:
            return html.unescape(match.group(1)).strip() or None
    return None


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
        with urlopen(request, timeout=20) as response:
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
    thumbnail_path = download_thumbnail(selected_video_info or metadata, workdir)

    audio_path = None
    if video_path is None and transcription_enabled():
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
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
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
    value = os.getenv("REELBOT_ENABLE_TRANSCRIPTION", "").strip().lower()
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
        transcript = " ".join(segment.text.strip() for segment in segments if segment.text).strip()
    except StageError:
        raise
    except Exception as exc:
        raise StageError("audio", f"faster-whisper transcription failed: {exc}") from exc

    (workdir / "transcript.txt").write_text(transcript, encoding="utf-8")
    return transcript


def sample_frames(video_path: Path, workdir: Path) -> list[Path]:
    frames_dir = workdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(frames_dir.glob("frame_*.jpg"))
    if existing:
        return existing

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=1/2",
        str(frames_dir / "frame_%05d.jpg"),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        raise StageError("ocr", f"ffmpeg frame sampling failed: {exc}") from exc

    if proc.returncode != 0:
        raise StageError("ocr", f"ffmpeg frame sampling failed: {proc.stderr.strip()}")

    return sorted(frames_dir.glob("frame_*.jpg"))


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


def anthropic_text_from_response(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif isinstance(block, dict) and block.get("text") is not None:
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def parse_structured_json(text: str) -> dict[str, Any]:
    candidate = strip_json_fences(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(candidate[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object")
    return normalize_structured(parsed)


def nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "unknown", "n/a"}:
        return None
    return text


def normalize_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def normalize_structured(data: dict[str, Any]) -> dict[str, Any]:
    has_place = normalize_bool(data.get("has_place"))
    has_content = normalize_bool(data.get("has_content", True)) or has_place

    tags = data.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(tag).strip() for tag in tags if str(tag).strip()]

    try:
        confidence = float(data.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    price_tier = nullable_string(data.get("price_tier"))
    if price_tier not in {"$", "$$", "$$$"}:
        price_tier = None

    return {
        "has_content": has_content,
        "content_type": nullable_string(data.get("content_type")),
        "title": nullable_string(data.get("title")),
        "has_place": has_place,
        "place_name": nullable_string(data.get("place_name")),
        "location_text": nullable_string(data.get("location_text")),
        "category": nullable_string(data.get("category")),
        "price_tier": price_tier,
        "tags": tags,
        "confidence": confidence,
    }


def stage_structure(caption: str, transcript: str, ocr_text: str, workdir: Path) -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise StageError("structure", "ANTHROPIC_API_KEY is missing")

    user_message = f"""
{SCHEMA_PROMPT}

caption:
{caption or ""}

transcript:
{transcript or ""}

ocr_text:
{ocr_text or ""}
""".strip()

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=800,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise StageError("structure", f"Anthropic structuring call failed: {exc}") from exc

    raw_text = anthropic_text_from_response(response)
    (workdir / "structured.raw.txt").write_text(raw_text, encoding="utf-8")

    try:
        structured = parse_structured_json(raw_text)
    except Exception as exc:
        raise StageError("structure", f"could not parse Anthropic JSON: {exc}") from exc

    write_json(workdir / "structured.json", structured)
    return structured


def stage_verify(structured: dict[str, Any], workdir: Path) -> dict[str, Any]:
    place_name = structured.get("place_name")
    location_text = structured.get("location_text")
    query = " ".join(part for part in [place_name, location_text] if part).strip()
    if not query:
        raise StageError("verify", "has_place=true but place_name/location_text were empty")

    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise StageError("verify", "GOOGLE_MAPS_API_KEY is missing")

    try:
        client = googlemaps.Client(key=api_key)
        response = client.places(query=query)
    except Exception as exc:
        raise StageError("verify", f"Google Places Text Search failed: {exc}") from exc
    finally:
        time.sleep(0.2)

    write_json(workdir / "places.raw.json", response)

    status = response.get("status")
    results = response.get("results") or []
    if status not in {"OK", "ZERO_RESULTS"}:
        raise StageError("verify", f"Google Places returned status {status}: {response}")
    if not results:
        raise StageError("verify", f"Google Places returned no results for query {query!r}")

    top = results[0]
    location = ((top.get("geometry") or {}).get("location") or {})
    verification = {
        "query": query,
        "canonical_name": top.get("name"),
        "place_id": top.get("place_id"),
        "dedupe_key": top.get("place_id"),
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "places_price_level": top.get("price_level"),
        "rating": top.get("rating"),
    }
    write_json(workdir / "verification.json", verification)
    return verification


def process_reel(url: str, workdir: str | Path) -> dict[str, Any]:
    workdir = Path(workdir)
    ingest = stage_ingest(url, workdir)
    if ingest.video_path is not None:
        transcript = stage_transcript(ingest.video_path, ingest.workdir)
        ocr_text = stage_ocr(ingest.video_path, ingest.workdir)
    else:
        if ingest.audio_path is not None:
            transcript = stage_transcript(ingest.audio_path, ingest.workdir)
        else:
            transcript = ""
            (ingest.workdir / "transcript.txt").write_text(transcript, encoding="utf-8")
        ocr_text = stage_thumbnail_ocr(ingest.thumbnail_path, ingest.workdir)
    structured = stage_structure(ingest.caption, transcript, ocr_text, ingest.workdir)

    base = {
        "source_url": url,
        "reel_id": ingest.reel_id,
        "caption": ingest.caption,
        "transcript": truncate(transcript, TRANSCRIPT_SAVE_CHARS),
        "ocr_text": truncate(ocr_text, OCR_SAVE_CHARS),
        "has_content": bool(structured.get("has_content")),
        "content_type": structured.get("content_type"),
        "title": structured.get("title"),
        "has_place": bool(structured.get("has_place")),
        "place_name": structured.get("place_name"),
        "location_text": structured.get("location_text"),
        "category": structured.get("category"),
        "price_tier": structured.get("price_tier"),
        "tags": structured.get("tags") or [],
        "confidence": structured.get("confidence"),
        "lat": None,
        "lng": None,
        "place_id": None,
        "metadata_only": ingest.metadata_only,
    }

    if not structured.get("has_place"):
        return base

    try:
        verification = stage_verify(structured, ingest.workdir)
    except StageError as exc:
        LOG.warning("Place verification failed; saving without map data: %s", exc.message)
        return base
    base.update(
        {
            "place_name": verification.get("canonical_name") or structured.get("place_name"),
            "lat": verification.get("lat"),
            "lng": verification.get("lng"),
            "place_id": verification.get("place_id"),
            "verification": verification,
        }
    )
    return base
