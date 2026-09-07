# ReelBot

ReelBot saves useful things from Instagram, TikTok, and YouTube: places, workouts, recipes, products, travel ideas, home projects, style, media, and learning. Recent opens first. Entries with a linked place also appear on the map. Your library works without an account; Apple sign-in is optional for cross-device sync.

The Expo / React Native app stores its library and outbox in SQLite. The native iOS share extension writes an atomic URL/timestamp file to the App Group container and completes immediately. It does not authenticate or make network requests. The main app acknowledges a shared file only after committing it locally, then uploads queued saves when connectivity and iOS execution time are available.

FastAPI enforces personal ownership. One Python pipeline gathers captions, OCR and audio, makes one cached, structured extraction request, persists every candidate, optionally resolves named venues, then files entries. `config/content_types.yaml` is the runtime taxonomy for extraction, validation, folders, filters and detail fields. The app receives registry updates through sync and bundles a copy for its first offline launch. A category can be added without an app rebuild or category-specific code.

## Local development

1. Install Python 3.12, Node, PostgreSQL with pgvector, ffmpeg, and tesseract. Install `requirements.txt` into a virtual environment.
2. Copy `.env.example` to `.env` and configure the database and provider credentials. Provider secrets never belong in Expo public settings.
3. For a fresh database apply `db/schema.sql`. For an existing database take a restorable backup, run `python db/migrate.py --dry-run`, then `python db/migrate.py`. Migration 001 preserves historical ownership, 002 upgrades personal places to typed entries, and 003 fixes the validation function search path. Each migration is transactional and forward-only; restore the backup to roll back.
4. Run `uvicorn api.main:app --host 127.0.0.1 --port 8000` and `python -m worker.worker` in separate processes. For a phone, bind the API to the Mac's reachable local address and configure the app with that address.
5. In `app/`, install dependencies, configure `.env`, and run `npm run prebuild:ios`. This uses a clean prebuild to avoid duplicate extension targets. Open the generated `ReelBot.xcworkspace` with the shared-container signing entitlements.

Each processing attempt has a 55-second supervisor deadline. Signal collection gets 30 seconds and keeps usable partial evidence; extraction has a bounded timeout. All candidates are stored before external lookups. A successful extraction is reused on retry while the registry version matches, preserving user edits and avoiding another model call. Expired leases become visible failures and receive one automatic retry. Offline saves remain local until the app can run and connect.

Type folders and facet subfolders appear only when needed. Multi-valued facets file an entry into every matching subfolder. Reviews stay in their folders; only uncertain cards show a specific question. Dismissal persists across restarts. Custom folders support rename, ordering, bulk copy/move and deletion without deleting entries. Search includes titles, summaries, attributes, notes, folder names and linked place names, with optional 384-dimensional semantic ranking.

The map uses native Apple Maps, filters out entries without coordinates, clusters at low zoom, and offers distance filters and an ascending Near me list. Location is requested only on the first Map visit after a rationale. Denying it leaves saved places available. Shipping builds have Recent, Map and Settings; Debug also exposes the isolated interest view.

## Verification

Use a disposable loopback database whose name contains `test`. The migration test also resets `reelbot_migration_test` on that local server and reads archived schemas from the retained Git history. Run `TEST_DATABASE_URL=… scripts/run_checks.sh`. Never point this command at a live database.

Run `DATABASE_URL=… python -m evals.run_golden` for the 40-source provider benchmark. Incomplete independent labels fail acceptance even when a provisional prediction matches. `VERIFICATION.md` and `audit-evidence/` record executed results and limitations. Provider costs are measured tokens/requests multiplied by configured rates; local compute and unavailable provider usage are not silently treated as measured fees.
