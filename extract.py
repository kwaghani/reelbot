#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import logging
import math
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

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")

try:
    import anthropic
    import googlemaps
    import pytesseract
    import yt_dlp
    from dotenv import load_dotenv
    from faster_whisper import WhisperModel
    from PIL import Image
except ImportError as exc:
    raise SystemExit(
        f"Missing dependency: {exc}. Run `pip install -r requirements.txt` in your venv."
    ) from exc


ROOT = Path(__file__).resolve().parent
REELS_PATH = ROOT / "reels.txt"
OUT_DIR = ROOT / "out"
RESULTS_PATH = ROOT / "results.json"
URL_INDEX_PATH = OUT_DIR / "url_index.json"
TRANSCRIPT_SAVE_CHARS = 500
OCR_SAVE_CHARS = 500
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "You are extracting a real-world place/activity from social content; "
    "respond with ONLY a JSON object, no prose, no markdown fences."
)

SCHEMA_PROMPT = """
Return exactly one JSON object with this schema:
{
  "has_place": bool,
  "place_name": str|null,
  "location_text": str|null,
  "category": str|null,
  "price_tier": str|null,
  "tags": [str],
  "confidence": float
}

Use has_place=false when there is no identifiable real place/activity.
price_tier must be "$", "$$", "$$$", or null.
confidence is 0..1 and is your confidence in place_name.
""".strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("reel-place-spike")

_WHISPER_MODEL: WhisperModel | None = None


class StageError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


@dataclass
class IngestResult:
    reel_id: str
    workdir: Path
    video_path: Path
    info: dict[str, Any]
    caption: str


def read_urls(path: Path) -> list[str]:
    if not path.exists():
        LOG.warning("Input file missing: %s", path)
        return []

    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Could not read JSON %s: %s", path, exc)
        return default


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


def safe_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return safe or f"url_{url_hash(value)}"


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
        (p for p in workdir.iterdir() if is_probable_video_file(p)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def extract_caption(info: dict[str, Any]) -> str:
    description = str(info.get("description") or "").strip()
    title = str(info.get("title") or "").strip()
    if description and title and title not in description:
        return f"{title}\n{description}"
    return description or title


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


def stage_ingest(url: str, url_index: dict[str, str]) -> IngestResult:
    cached_work_id = url_index.get(url)
    if cached_work_id:
        workdir = OUT_DIR / cached_work_id
        info_path = workdir / "info.json"
        video_path = find_video_file(workdir)
        info = load_json(info_path, {})
        if video_path and info:
            reel_id = str(info.get("id") or cached_work_id)
            caption = extract_caption(info)
            LOG.info("[%s] ingest cache hit", reel_id)
            return IngestResult(reel_id, workdir, video_path, info, caption)

    metadata = ytdlp_extract(url, download=False)
    reel_id = str(metadata.get("id") or f"url_{url_hash(url)}")
    work_id = safe_path_part(reel_id)
    workdir = OUT_DIR / work_id
    workdir.mkdir(parents=True, exist_ok=True)
    info_path = workdir / "info.json"
    write_json(info_path, metadata)
    selected_video_info = select_video_info(metadata)
    if selected_video_info is not metadata:
        write_json(workdir / "selected_video_info.json", selected_video_info)
        LOG.info(
            "[%s] selected child video %s",
            reel_id,
            selected_video_info.get("id") or "unknown",
        )

    video_path = find_video_file(workdir)
    if video_path:
        info = metadata
        LOG.info("[%s] video cache hit", reel_id)
    else:
        LOG.info("[%s] downloading source video", reel_id)
        download_video_info(selected_video_info, url, workdir)
        info = metadata
        video_path = find_video_file(workdir)

        if video_path is None:
            raise StageError("ingest", "yt-dlp completed but no source video was found")

    url_index[url] = work_id
    caption = extract_caption(info)
    (workdir / "caption.txt").write_text(caption, encoding="utf-8")
    return IngestResult(reel_id, workdir, video_path, info, caption)


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


def get_whisper_model() -> WhisperModel:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL

    try:
        _WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    except Exception as exc:
        raise StageError("audio", f"faster-whisper model load failed: {exc}") from exc
    return _WHISPER_MODEL


def stage_transcript(video_path: Path, workdir: Path) -> str:
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


def normalize_structured(data: dict[str, Any]) -> dict[str, Any]:
    has_place = data.get("has_place")
    if isinstance(has_place, str):
        has_place = has_place.strip().lower() in {"true", "yes", "1"}
    else:
        has_place = bool(has_place)

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


def base_result(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "reel_id": f"url_{url_hash(url)}",
        "caption": "",
        "transcript": "",
        "ocr_text": "",
        "structured": None,
        "verification": None,
        "status": "",
        "error": None,
    }


def mark_error(result: dict[str, Any], exc: StageError | Exception, stage: str | None = None) -> dict[str, Any]:
    error_stage = getattr(exc, "stage", None) or stage or "unknown"
    message = strip_ansi(getattr(exc, "message", None) or str(exc))
    LOG.error("[%s] %s failed: %s", result.get("reel_id"), error_stage, message)
    result["status"] = f"error:{error_stage}"
    result["error"] = {"stage": error_stage, "message": message}
    return result


def process_url(url: str, url_index: dict[str, str]) -> dict[str, Any]:
    result = base_result(url)
    caption = ""
    transcript = ""
    ocr_text = ""

    try:
        ingest = stage_ingest(url, url_index)
        result["reel_id"] = ingest.reel_id
        caption = ingest.caption
        result["caption"] = caption
    except StageError as exc:
        return mark_error(result, exc)
    except Exception as exc:
        return mark_error(result, exc, "ingest")

    try:
        transcript = stage_transcript(ingest.video_path, ingest.workdir)
        result["transcript"] = truncate(transcript, TRANSCRIPT_SAVE_CHARS)
    except StageError as exc:
        return mark_error(result, exc)
    except Exception as exc:
        return mark_error(result, exc, "audio")

    try:
        ocr_text = stage_ocr(ingest.video_path, ingest.workdir)
        result["ocr_text"] = truncate(ocr_text, OCR_SAVE_CHARS)
    except StageError as exc:
        return mark_error(result, exc)
    except Exception as exc:
        return mark_error(result, exc, "ocr")

    try:
        structured = stage_structure(caption, transcript, ocr_text, ingest.workdir)
        result["structured"] = structured
    except StageError as exc:
        return mark_error(result, exc)
    except Exception as exc:
        return mark_error(result, exc, "structure")

    if not structured.get("has_place"):
        result["status"] = "no_place"
        result["error"] = None
        return result

    try:
        verification = stage_verify(structured, ingest.workdir)
        result["verification"] = verification
        result["status"] = "verified"
        result["error"] = None
    except StageError as exc:
        return mark_error(result, exc)
    except Exception as exc:
        return mark_error(result, exc, "verify")

    return result


def save_results(results: list[dict[str, Any]]) -> None:
    write_json(RESULTS_PATH, results)


def print_grading_table(results: list[dict[str, Any]]) -> None:
    print()
    print("reel_id | status | place_name \u2192 canonical_name | confidence")
    print("-" * 78)
    for result in results:
        structured = result.get("structured") or {}
        verification = result.get("verification") or {}
        place_name = structured.get("place_name") or "-"
        canonical_name = verification.get("canonical_name") or "-"
        confidence = structured.get("confidence")
        if isinstance(confidence, (int, float)):
            confidence_text = f"{confidence:.2f}"
        else:
            confidence_text = "-"
        print(
            f"{result.get('reel_id') or '-'} | "
            f"{result.get('status') or '-'} | "
            f"{place_name} \u2192 {canonical_name} | "
            f"{confidence_text}"
        )

    verified = sum(1 for result in results if result.get("status") == "verified")
    no_place = sum(1 for result in results if result.get("status") == "no_place")
    errors = sum(1 for result in results if str(result.get("status", "")).startswith("error:"))
    total = len(results)
    clean = verified + no_place

    if total == 0:
        print()
        print("Summary: verified=0, no_place=0, error=0")
        print("Clean outcomes: 0/0 (threshold=N/A)")
        print("GATE: FAIL \u2014 add at least one URL to reels.txt before judging.")
        return

    threshold = math.ceil(0.66 * total)

    print()
    print(f"Summary: verified={verified}, no_place={no_place}, error={errors}")
    print(f"Clean outcomes: {clean}/{total} (threshold={threshold})")

    if clean >= threshold:
        print("GATE: PASS \u2014 proceed to Phase 1")
    else:
        print("GATE: FAIL \u2014 fix ingestion before building.")


def main() -> int:
    load_dotenv(ROOT / ".env")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    urls = read_urls(REELS_PATH)
    url_index = load_json(URL_INDEX_PATH, {})
    if not isinstance(url_index, dict):
        url_index = {}

    results: list[dict[str, Any]] = []
    for index, url in enumerate(urls, start=1):
        LOG.info("Processing %s/%s: %s", index, len(urls), url)
        result = process_url(url, url_index)
        results.append(result)
        save_results(results)
        write_json(URL_INDEX_PATH, url_index)

    save_results(results)
    write_json(URL_INDEX_PATH, url_index)
    print_grading_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
