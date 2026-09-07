"""Accepted source links and stable identities shared by API and extraction.

Only individual content URLs are accepted; profiles, search pages and arbitrary
subdomains must never become saved reels. Tracking parameters are not identity.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

URL_ERROR = "Use an Instagram reel/post, TikTok video, or YouTube video/Shorts link."


def canonical_reel_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() not in {"https", "http"} or parsed.username or parsed.password or parsed.port:
            raise ValueError(URL_ERROR)
    except (ValueError, AttributeError) as exc:
        raise ValueError(URL_ERROR) from exc
    path = parsed.path.rstrip("/")
    if re.search(r"[\s<>\\\"']", value):
        raise ValueError(URL_ERROR)
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        match = re.fullmatch(r"/(?:reel|reels|p)/([A-Za-z0-9_-]+)", path)
        if match:
            kind = "p" if path.startswith("/p/") else "reel"
            return f"https://www.instagram.com/{kind}/{match[1]}/"
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        match = re.fullmatch(r"/@([\w.-]+)/video/(\d+)", path)
        if match:
            return f"https://www.tiktok.com/@{match[1]}/video/{match[2]}"
        match = re.fullmatch(r"/t/([A-Za-z0-9]+)", path)
        if match:
            return f"https://www.tiktok.com/t/{match[1]}/"
    if host in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[A-Za-z0-9]+", path):
        return f"https://{host}{path}/"
    video_id = None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        match = re.fullmatch(r"/(?:shorts|watch)/([A-Za-z0-9_-]{11})", path)
        if match:
            video_id = match[1]
        elif path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
    elif host == "youtu.be":
        video_id = path.lstrip("/")
    if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    raise ValueError(URL_ERROR)


def content_identity(url: str) -> str:
    """Namespace content IDs by platform, even when extractor IDs coincide."""
    import hashlib

    canonical = canonical_reel_url(url)
    return "content_" + hashlib.sha256(canonical.encode()).hexdigest()
