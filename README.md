# ReelBot

ReelBot saves useful things from Instagram, TikTok, and YouTube: places, workouts, recipes, products, travel ideas, home projects, style, media, and learning. Recent opens first. Entries with a linked place also appear on the map. Your library works without an account; Apple sign-in is optional for cross-device sync.

The Expo / React Native app stores its library and outbox in SQLite. The native iOS share extension writes an atomic URL/timestamp file to the App Group container and completes immediately. It does not authenticate or make network requests. The main app acknowledges a shared file only after committing it locally, then uploads queued saves when connectivity and iOS execution time are available.

FastAPI enforces personal ownership. One Python pipeline gathers captions, OCR and audio, makes one cached, structured extraction request, persists every candidate, optionally resolves named venues, then files entries. `config/content_types.yaml` is the runtime taxonomy for extraction, validation, folders, filters and detail fields. The app receives registry updates through sync and bundles a copy for its first offline launch. A category can be added without an app rebuild or category-specific code.

## Local development

1. Install Python 3.12, Node, PostgreSQL with pgvector, ffmpeg, and tesseract. Install `requirements.txt` into a virtual environment.
2. Copy `.env.example` to `.env` and configure the database and provider credentials. Provider secrets never belong in Expo public settings.
3. For a fresh database apply `db/schema.sql`. For an existing database take a restorable backup, run `python db/migrate.py --dry-run`, then `python db/migrate.py`. Migration 001 preserves historical ownership, 002 upgrades personal places to typed entries, 003 fixes the validation function search path, and 20260908014221 adds ingestion identities, caches and states. Each migration is transactional and forward-only; restore the backup to roll back.
4. Run `uvicorn api.main:app --host 127.0.0.1 --port 8000` and `python -m worker.worker` in separate processes. For a phone, bind the API to the Mac's reachable local address and configure the app with that address.
5. In `app/`, install dependencies, configure `.env`, and run `npm run prebuild:ios`. This uses a clean prebuild to avoid duplicate extension targets. Open the generated `ReelBot.xcworkspace` with the shared-container signing entitlements.

Each processing attempt has a 145-second supervisor deadline and a 150-second lease. URL resolution is limited to five hops and ten seconds. The fetch ladder tries public oEmbed, structured HTML/metadata, then bounded media; collection keeps partial checkpoints and expires after 96 seconds. Media files are capped at 50 MB; videos beyond 90 seconds use frames only. Six frame samples feed OCR and, when needed, vision. All temporary media is deleted. A tagged POI skips venue inference; otherwise one classifier receives provenance-labelled signals and deterministic hints.

Original shared URLs remain intact alongside canonical URLs and platform video IDs. Fetch results and public extractions are cached for 30 days, with concurrent misses serialized. `config/ingestion.yaml` holds the browser headers, rate limits, city hashtag mapping and centroids. `REELBOT_FETCH_PROXY` is optional and off by default; `INSTAGRAM_OEMBED_TOKEN` enables Instagram oEmbed. Requests are rate limited per platform across workers. Captured captions never become “no content”: blocked, missing, unreadable and extraction-empty states remain distinct. Blocked fetches retry after 1 minute, 15 minutes, 2 hours and 12 hours, then offer manual entry. Waiting in the durable queue never expires a save.

Place matching uses the five required Text Search fields and normalized name similarity above 0.7. A known city imposes a 20 km radius even on the final unbiased query; out-of-radius results become one-tap review choices. Hours and ratings are fetched only when requested. All extracted candidates are stored before geocoding. Manual entries, edits and place choices use the durable personal outbox. Long-press a card in Debug to inspect URLs, tier logs, raw signals, complete text prompts/responses, place queries and measured costs; Release excludes this view.

After backing up an existing database and applying the migration, run `python -m worker.backfill_ingestion --output /path/to/backfill.json`, then run the worker. The backfill preserves entries and notes, restores original URLs from queued-job records where available, fills canonical identities and queues existing failures/reviews. Record the resulting states before calling a failed save recovered.

Type folders and facet subfolders appear only when needed. Multi-valued facets file an entry into every matching subfolder. Reviews stay in their folders; only uncertain cards show a specific question. Dismissal persists across restarts. Custom folders support rename, ordering, bulk copy/move and deletion without deleting entries. Search includes titles, summaries, attributes, notes, folder names and linked place names, with optional 384-dimensional semantic ranking.

The map uses native Apple Maps, filters out entries without coordinates, clusters at low zoom, and offers distance filters and an ascending Near me list. Location is requested only on the first Map visit after a rationale. Denying it leaves saved places available. Shipping builds have Recent, Map and Settings; Debug also exposes the isolated interest view.

## Verification

Use a disposable loopback database whose name contains `test`. The migration test also resets `reelbot_migration_test` on that local server and reads archived schemas from the retained Git history. Run `TEST_DATABASE_URL=… scripts/run_checks.sh`. Never point this command at a live database.

Run `DATABASE_URL=… python -m evals.run_golden` for the 40-source provider benchmark. Incomplete independent labels fail acceptance even when a provisional prediction matches. `VERIFICATION.md` and `audit-evidence/` record executed results and limitations. Provider costs are measured tokens/requests multiplied by configured rates; local compute and unavailable provider usage are not silently treated as measured fees.

Run `INGESTION_EVAL_DATABASE_URL=… python -m evals.run_ingestion` on a fresh disposable loopback database for the fixed 30-link ingestion fixture. Public URL eligibility and independently reviewed labels are recorded separately from extraction output. See `ingestion-evidence/REPORT.md` for executed results, costs and unmet acceptance targets.
