"""Low-cost process and queue diagnostics for bounded worker operation."""
from __future__ import annotations

import logging
from pathlib import Path

LOG = logging.getLogger("reelbot.worker")


def log_rss(point: str, save_id: object) -> None:
    try:
        # Linux reports resident pages in KiB; this works in the Render container.
        rss_kib = int(Path("/proc/self/status").read_text().split("VmRSS:", 1)[1].split()[0])
        LOG.info("worker_rss point=%s save=%s rss_kib=%s", point, save_id, rss_kib)
    except Exception:
        LOG.warning("worker_rss point=%s save=%s unavailable=true", point, save_id)


def queue_depth() -> int:
    from worker.db import connect
    with connect() as conn:
        return int(conn.execute("select count(*) as n from jobs where status in ('queued','processing')").fetchone()["n"])
