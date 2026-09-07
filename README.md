# ReelBot

ReelBot is a private-test shared reel organizer: an Expo iOS app with a share
extension, a WhatsApp group bot, and a Python API/worker backed by Postgres.
It stores structured information and source links for Instagram posts/reels,
TikTok videos, and YouTube videos/Shorts. Playback opens the original platform;
there is no hosted video player or offline video library.

The iOS app supports a stored display name and device session, a shared default
library, invite-code groups, copying reels between groups, automatic topic and
location folders, group-wide deletion, and persistent local chat with saved-reel
sources. All group members can add or delete records. Copies in other groups
are independent. Verified venues merge by place ID within a group; non-place
content merges by canonical platform URL. There are no email/password accounts,
public reel pages, comments, reactions, manual folder editing, or email/push alerts.

See [AUDIT.md](AUDIT.md) for verified behavior and remaining limits, and
[VERIFICATION.md](VERIFICATION.md) for repeatable local checks and rollout steps.

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
ANTHROPIC_MODEL=claude-fable-5
ANTHROPIC_FAST_MODEL=claude-sonnet-5
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
GOOGLE_MAPS_API_KEY=...
IG_COOKIES_PATH=
REELBOT_ENABLE_VIDEO_DOWNLOAD=false
REELBOT_ENABLE_VISION=true
TARGET_GROUP_JID=
NUDGE_INTERVAL_HOURS=3
NUDGE_COOLDOWN_DAYS=3
NUDGE_CLUSTER_COOLDOWN_DAYS=14
NUDGE_MIN_ITEMS=3
NUDGE_RECENCY_DAYS=7
NUDGE_IMPACT_WINDOW_HOURS=24
```

ReelBot stores structured reel data plus the original reel URL. The extractor preserves a concise summary, concrete details, tags, and available caption/transcript/OCR text so chat can answer questions about recipes, workouts, sports, relationships, jokes, products, and other saved content—not only places. It does not store reel videos offline. By default `REELBOT_ENABLE_VIDEO_DOWNLOAD=false`, so ingest uses public page metadata, captions, thumbnails, OCR, and the source link instead of downloading media.

`ANTHROPIC_MODEL` defaults to `claude-fable-5`, the project's configured quality model, for final answers. The faster
`ANTHROPIC_FAST_MODEL` defaults to `claude-sonnet-5` for extraction, routing,
folder assignment, and grounded-answer verification. This split puts the most
capable model on user-visible reasoning without paying Fable latency for every
classification call.

`EMBEDDING_MODEL` defaults to `BAAI/bge-small-en-v1.5`. It keeps the existing
384-dimensional pgvector schema while improving semantic retrieval. If this
value changes for an existing database, rebuild every stored vector before
serving queries:

```bash
source .venv/bin/activate
python worker/reindex_embeddings.py --all-groups --apply
```

`REELBOT_ENABLE_VISION=true` lets extraction send up to three compressed,
representative reel frames (or the thumbnail) alongside the caption,
transcript, and OCR. Set it to `false` to use text-only extraction.

`IG_COOKIES_PATH` is optional and only matters if you later set `REELBOT_ENABLE_VIDEO_DOWNLOAD=true`. Instagram video downloads often need authenticated cookies. It can be an exported Netscape cookies file path or a yt-dlp browser source such as `browser:chrome`.

`TARGET_GROUP_JID` is optional. If set, the listener ignores all WhatsApp groups except that JID.

`API_KEY` and `TEST_GROUP_ID` configure the private-test iOS API. The default
library is visible to every tester with a valid device session. `POST /devices`
issues a random device ID and bearer token; all data routes require that token
in addition to the build key. Device IDs and display names alone confer no
access. Private groups require an invite and membership. Apply the schema and
rebuild the client together; legacy device IDs cannot securely claim sessions.
Existing group records are retained, but users must rejoin with an invite code.
This remains device-based test access, not recoverable multi-device accounts.

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

To review how the latest classifier would reorganize an existing group's
library without changing data:

```bash
source .venv/bin/activate
python worker/organize_library.py
```

After reviewing the proposed moves, rerun it with `--apply` to persist the new
folders and rebuild embeddings for changed items.

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
- iOS `POST /query`: answers within the authenticated selected group; queued queries return a job ID for polling when still processing.
- iOS `GET /items`: lists the authorized selected library, including queued imports and readable failures, with distinct device saver counts and timestamps.
- The scheduler periodically scans each group for one worthwhile saved-item cluster, respects per-group and per-cluster cooldowns, and queues a single `outbound_messages` row when a nudge is warranted.
- The listener does not call LLMs, extract video, embed text, or do retrieval.
- The listener also polls unsent `outbound_messages`, sends them through Baileys, then marks `sent_at`.
- The worker extracts reel page metadata, verifies places through Google Places, stores the original source URL, dedupes by `(group_id, place_id)`, records each saver in `item_saves`, stores a 384-dim embedding, and writes `save`, `query`, and `error` events.
- The scheduler records queued nudges and links them to an outbox row; the listener records transport acceptance. Impact reports count only messages accepted by the transport, not unsent queues.

## Nudge Impact

Run the impact report to see whether nudges lead to a `query` or `save` event in the configured window:

```bash
source .venv/bin/activate
python evals/nudge_impact.py
```

## Acceptance Checks

- Sharing a reel with a clear place replies `Saved → <name> (<list>)`; `items.place_id` and `items.embedding` are populated.
- Sharing the same place again from another member adds an `item_saves` row without duplicating `items`.
- Understandable non-place reels are saved with a title and topic folder. Unavailable or unreadable reels fail visibly and create no item.
- Asking `what should we do in <place>?` returns a short answer grounded only in saved items.
- With 3+ recently active saved items in a group and no cooldown conflict, `python worker/scheduler.py --once` creates exactly one grounded nudge in `outbound_messages`, records it in `nudges`, and logs a `nudge` event.
- A group nudged within `NUDGE_COOLDOWN_DAYS`, or a cluster nudged within `NUDGE_CLUSTER_COOLDOWN_DAYS`, gets skipped.
- `python evals/nudge_impact.py` reports nudge engagement conversion over `NUDGE_IMPACT_WINDOW_HOURS`.
- Any temporary thumbnails or optional media files are deleted after every ingest job.
- A broken reel URL marks the job `error`, records an `error` event, and the worker keeps polling.
