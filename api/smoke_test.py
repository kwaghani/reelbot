from __future__ import annotations

import os
import sys
import types
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="Using `httpx`.*", category=Warning)

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["API_KEY"] = "smoke-test-key"
os.environ["TEST_GROUP_ID"] = "00000000-0000-0000-0000-000000000001"

import api.main as api_main


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.events: list[tuple[str | None, str, str | None]] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> FakeResult:
        normalized = " ".join(sql.lower().split())
        if "insert into jobs" in normalized:
            params = list(params or [])
            self.jobs.append(
                {
                    "group_id": params[0],
                    "chat_id": "app",
                    "sender_id": params[1],
                    "type": "ingest",
                    "payload": params[2],
                    "status": "queued",
                }
            )
        if "from items i" in normalized:
            return FakeResult(
                [
                    {
                        "place_name": "Sugo Social",
                        "category": "dining",
                        "location_text": "Los Angeles",
                        "list_name": "LA food",
                        "lat": 34.05,
                        "lng": -118.24,
                        "price_tier": "$$",
                        "tags": ["italian", "date night"],
                        "save_count": 2,
                    }
                ]
            )
        return FakeResult()

    def commit(self) -> None:
        return None


fake_conn = FakeConnection()


@contextmanager
def fake_connect():
    yield fake_conn


def fake_member(
    _conn: Any,
    group_id: str,
    wa_user_id: str,
    display_name: str | None = None,
) -> dict[str, str | None]:
    return {"id": "member-1", "group_id": group_id, "wa_user_id": wa_user_id, "display_name": display_name}


def fake_log_event(_conn: Any, group_id: str | None, kind: str, detail: str | None = None) -> None:
    fake_conn.events.append((group_id, kind, detail))


fake_retrieval = types.ModuleType("retrieval")
fake_retrieval.answer_question = lambda group_id, text: f"grounded answer for {text} in {group_id}"
sys.modules["retrieval"] = fake_retrieval

api_main.load_settings.cache_clear()
api_main.connect = fake_connect
api_main.get_or_create_member = fake_member
api_main.log_event = fake_log_event

client = TestClient(api_main.app)
headers = {"x-api-key": "smoke-test-key"}


def assert_response(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    unauthorized = client.get("/items")
    assert_response(unauthorized.status_code == 401, "missing API key should be rejected")

    bad_share = client.post("/share", headers=headers, json={"url": "not-a-reel", "user_name": "Krish"})
    assert_response(bad_share.status_code == 400, "bad share URL should be rejected")

    share = client.post(
        "/share",
        headers=headers,
        json={"url": "https://www.instagram.com/reel/ABC123/", "user_name": "Krish"},
    )
    assert_response(share.status_code == 202, f"share returned {share.status_code}")
    assert_response(share.json() == {"status": "queued"}, "share response should be queued")
    assert_response(fake_conn.jobs[-1]["type"] == "ingest", "share should enqueue ingest job")
    assert_response(fake_conn.jobs[-1]["chat_id"] == "app", "share job chat_id should be app")

    query = client.post("/query", headers=headers, json={"text": "what is saved?", "user_name": "Krish"})
    assert_response(query.status_code == 200, f"query returned {query.status_code}")
    assert_response("grounded answer" in query.json()["answer"], "query should return retrieval answer")
    assert_response(fake_conn.events[-1][1] == "query", "query should log event")

    items = client.get("/items", headers=headers)
    assert_response(items.status_code == 200, f"items returned {items.status_code}")
    body = items.json()
    assert_response(body[0]["place_name"] == "Sugo Social", "items should include saved place")
    assert_response(body[0]["save_count"] == 2, "items should include save_count")

    print("API smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
