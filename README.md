# Shared Reel Bot Phase 1

This is the thinnest end-to-end shared reel organizer: a WhatsApp group bot that silently ingests Instagram/TikTok reel links, answers questions from the group's saved places, and can send rare proactive saved-place nudges. There is no web app, mobile app, auth, voting, planning engine, or admin UI.

Phase 0 extraction still exists in `extract.py`. The worker reuses that pipeline logic through `worker/pipeline.py`, with per-reel media written only to a temporary directory that is deleted after each job.

## Layout

```text
db/schema.sql          Supabase Postgres + pgvector schema
api/main.py            FastAPI wrapper for the iOS app
worker/worker.py       Python queue worker for ingest/query jobs
worker/scheduler.py    Python scheduler for rare proactive retention nudges
worker/pipeline.py     Phase 0 extractor refactored as process_reel(url, workdir)
worker/retrieval.py    Simple group-scoped semantic retrieval + Anthropic answer
worker/embed.py        Local all-MiniLM-L6-v2 embeddings
worker/db.py           psycopg queries
listener/index.js      Baileys WhatsApp transport + queue/reply loop
listener/package.json  Node dependencies
app/                   Expo iOS app + share extension
evals/nudge_impact.py  Nudge-to-engagement conversion report
```

## Setup

1. Run `db/schema.sql` against your Supabase Postgres database. Use a private server-side Postgres connection string for the bot services.

2. Install Python dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r worker/requirements.txt
```

3. Install listener dependencies:

```bash
cd listener
npm install
```

4. Copy and fill the environment file:

```bash
cp .env.example .env
```

```bash
DATABASE_URL=postgres://...
API_KEY=shared-test-secret
TEST_GROUP_ID=00000000-0000-0000-0000-000000000000
ANTHROPIC_API_KEY=...
GOOGLE_MAPS_API_KEY=...
IG_COOKIES_PATH=
REELBOT_ENABLE_VIDEO_DOWNLOAD=false
TARGET_GROUP_JID=
NUDGE_INTERVAL_HOURS=3
NUDGE_COOLDOWN_DAYS=3
NUDGE_CLUSTER_COOLDOWN_DAYS=14
NUDGE_MIN_ITEMS=3
NUDGE_RECENCY_DAYS=7
NUDGE_IMPACT_WINDOW_HOURS=24
```

ReelBot stores extracted place data plus the original reel URL. It does not store reel videos offline. By default `REELBOT_ENABLE_VIDEO_DOWNLOAD=false`, so ingest uses public page metadata, captions, thumbnails, OCR, and the source link instead of downloading media.

`IG_COOKIES_PATH` is optional and only matters if you later set `REELBOT_ENABLE_VIDEO_DOWNLOAD=true`. Instagram video downloads often need authenticated cookies. It can be an exported Netscape cookies file path or a yt-dlp browser source such as `browser:chrome`.

`TARGET_GROUP_JID` is optional. If set, the listener ignores all WhatsApp groups except that JID.

`API_KEY` and `TEST_GROUP_ID` are used by the test iOS API. `TEST_GROUP_ID` must be a UUID. The API creates a matching `groups` row if absent. `API_KEY` is only lightweight test-grade protection and should not be exposed as a long-term public auth scheme.

## Run

Start the worker:

```bash
source .venv/bin/activate
python worker/worker.py
```

Start the nudge scheduler in another terminal if you want proactive retention nudges:

```bash
source .venv/bin/activate
python worker/scheduler.py
```

Start the listener in another terminal:

```bash
cd listener
npm start
```

On first run, Baileys prints a QR code. Scan it with the WhatsApp account that is already in your private test group. Baileys is an unofficial linked-device library; use this only for a private test group.

Start the HTTP API for the iOS app:

```bash
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Example systemd unit:

```ini
[Unit]
Description=Shared Reel Bot API
After=network.target

[Service]
WorkingDirectory=/opt/reelbot
EnvironmentFile=/opt/reelbot/.env
ExecStart=/opt/reelbot/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

See `app/README.md` for the Expo custom dev-client build and App Group setup.

## Behavior

- Group message with an Instagram or TikTok URL: queues an `ingest` job.
- Group question ending in `?` or starting with `what`, `where`, `which`, `should`, `find`, or `plan`: queues a `query` job.
- iOS `POST /share`: queues the same `ingest` job shape with `chat_id='app'`.
- iOS `POST /query`: returns `retrieval.answer_question(TEST_GROUP_ID, text)` directly.
- iOS `GET /items`: lists the test group's saved places with distinct saver counts.
- The scheduler periodically scans each group for one worthwhile saved-item cluster, respects per-group and per-cluster cooldowns, and queues a single `outbound_messages` row when a nudge is warranted.
- The listener does not call LLMs, extract video, embed text, or do retrieval.
- The listener also polls unsent `outbound_messages`, sends them through Baileys, then marks `sent_at`.
- The worker extracts reel page metadata, verifies places through Google Places, stores the original source URL, dedupes by `(group_id, place_id)`, records each saver in `item_saves`, stores a 384-dim embedding, and writes `save`, `query`, and `error` events.
- The scheduler records each sent nudge in `nudges` and writes a `nudge` event with the cluster key.

## Nudge Impact

Run the impact report to see whether nudges lead to a `query` or `save` event in the configured window:

```bash
source .venv/bin/activate
python evals/nudge_impact.py
```

## Acceptance Checks

- Sharing a reel with a clear place replies `Saved → <name> (<list>)`; `items.place_id` and `items.embedding` are populated.
- Sharing the same place again from another member adds an `item_saves` row without duplicating `items`.
- Sharing a no-place reel replies `Couldn't find a place in that one 🤔` and creates no item.
- Asking `what should we do in <place>?` returns a short answer grounded only in saved items.
- With 3+ recently active saved items in a group and no cooldown conflict, `python worker/scheduler.py --once` creates exactly one grounded nudge in `outbound_messages`, records it in `nudges`, and logs a `nudge` event.
- A group nudged within `NUDGE_COOLDOWN_DAYS`, or a cluster nudged within `NUDGE_CLUSTER_COOLDOWN_DAYS`, gets skipped.
- `python evals/nudge_impact.py` reports nudge engagement conversion over `NUDGE_IMPACT_WINDOW_HOURS`.
- Any temporary thumbnails or optional media files are deleted after every ingest job.
- A broken reel URL marks the job `error`, records an `error` event, and the worker keeps polling.
