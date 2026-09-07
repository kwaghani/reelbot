# ReelBot

ReelBot turns Instagram, TikTok, and YouTube links into a personal collection of places. The iOS app opens directly into a local library. Links, folders, and notes persist offline; processing resumes when the main app can reach the service. Apple sign-in is optional and enables cross-device sync.

The Expo / React Native app uses a small native share extension. The extension writes one atomic `{url, timestamp}` file in the App Group container and immediately completes. It performs no networking or authentication. The app imports those files into SQLite before acknowledging them, then uploads pending links with an anonymous device credential stored in Keychain. App Group preference read/write remains available in `app/src/sharedGroup.ts`.

FastAPI exposes owner-scoped saves, places, folders, notes, search, sync, and optional Apple verification. A Python worker independently combines captions, five OCR frames, and local audio transcription. One schema-constrained Claude request extracts an array of candidates. Every candidate is persisted before address lookup. Google Places Text Search uses exactly the five required fields; global cached venue matches are reused across libraries. Low-confidence or ambiguous results remain visible for review.

## Run locally

1. Install Python 3.12, Node, PostgreSQL with pgvector, ffmpeg, and tesseract. Install `requirements.txt` into a virtual environment.
2. Copy `.env.example` to `.env` and configure the database and provider credentials. Never put provider credentials into the app.
3. For a new database, apply `db/schema.sql`. For an existing database, take a restorable backup, run `python db/migrate.py --dry-run`, then `python db/migrate.py`. The migration is atomic and forward-only; restoring the backup is the rollback path.
4. Run `uvicorn api.main:app --host 0.0.0.0 --port 8000` and `python -m worker.worker` in separate processes.
5. In `app/`, install dependencies, copy `.env.example` to `.env`, and set the API URL. Run `npm run prebuild:ios`, then `npm run ios`. Native queue templates and their config plugin are tracked; generated `ios/` files are not.

The worker gives each attempt a maximum 55-second process deadline, with server queue wait included in the first attempt’s 58-second expiry. Media collection has a 30-second budget and preserves partial captions, OCR and transcripts for extraction. An interrupted lease becomes a visible failure and receives one automatic retry after 15 seconds. Manual Retry starts a fresh attempt budget. Local saves can remain queued offline until connectivity and iOS execution time are available.

Automatic city/category folders appear only when a place needs them. Custom folders support rename, deletion, ordering, and bulk copy/move. Deleting a folder preserves places. Search combines lexical matches for name/city/folder/note with retained 384-dimensional cosine similarity. A separate indexing process maintains embeddings without extending ingestion deadlines.

## Verify

Use a disposable loopback database with `test` in its name. The migration test also uses `reelbot_migration_test` in that same local database server. Run `TEST_DATABASE_URL=… scripts/run_checks.sh`. These tests reset their test data. Run `DATABASE_URL=… python -m evals.run_golden` for real provider evaluation; fixtures with incomplete human labeling fail acceptance.

See `VERIFICATION.md` for executed results and limits, `SHARE_TEST.md` for device verification, and `RUNBOOK.md` for operations. Debug cost figures multiply measured provider usage by configurable rates and exclude local compute; they are estimates, not invoices.
