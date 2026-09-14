# Render database cutover — September 14, 2026

## Verified transfer

Source: existing Supabase PostgreSQL 17 application database.
Destination: `reelbot-db`, Render PostgreSQL 18 in Oregon.

The destination was verified empty before restoring. A consistent, read-only
source snapshot was exported with `pg_dump --schema=public --no-owner
--no-privileges`, excluding Supabase-managed internal schemas. The target
restore was atomic and installed the required vector and pgcrypto extensions.
Supabase was not deleted or modified by the transfer.

All 24 tables (858 rows) matched by normalized SHA-256 content fingerprints.
The source and target use different floating-point output settings; these were
normalized before comparison. Coordinates additionally matched in their exact
binary representation. Device credentials and ownership data are preserved.

A private checkpoint was uploaded to R2 as
`backups/pre-cutover-2026-09-14.sql.gz` (661,373 bytes), downloaded, and verified
byte-for-byte. That downloaded backup restored successfully into a separate
Render database, `reelbot_restore_verify_20260914`; all 24 table counts and
normalized content fingerprints matched again. The scratch database remains
available for review and is not used by the services.

| Table | Source rows | Render rows |
| --- | ---: | ---: |
| city_bias_cache | 1 | 1 |
| content_type_registry | 10 | 10 |
| devices | 1 | 1 |
| entries | 14 | 14 |
| events | 59 | 59 |
| fetch_cache | 1 | 1 |
| fetch_rate_limits | 1 | 1 |
| folder_items | 28 | 28 |
| folders | 5 | 5 |
| image_assets | 13 | 13 |
| imagery_claims | 2 | 2 |
| imagery_runs | 591 | 591 |
| jobs | 58 | 58 |
| natural_geocode_cache | 0 | 0 |
| orphaned_items | 21 | 21 |
| photo_jobs | 12 | 12 |
| places | 12 | 12 |
| save_source_urls | 10 | 10 |
| saves | 8 | 8 |
| schema_migrations | 9 | 9 |
| source_url_cache | 1 | 1 |
| users | 1 | 1 |
| venue_identity_repairs | 0 | 0 |
| venue_kind_unmapped | 0 | 0 |

Render reports `max_connections=103`. API ceiling 10 plus worker ceiling 4
totals 14, leaving 89 connections outside those pools (75 remain even with
both old and new generations overlapping during deployment).

## Completed production cutover

- The approved maintenance window blocked public requests while connections
  were changed. All 24 source/target fingerprints matched immediately before
  cutover and again after both new services deployed. No source changes were
  lost. Maintenance mode is now disabled.
- Both service-level `DATABASE_URL` overrides now match the internal Render
  connection in `reelbot-shared`.
- Production code: `620b6ef25bd729da4355dabd1f62d25bf8a9b632` on
  `codex/venue-identity`.
- API deployment `dep-dajscim8h83s73anhekg` became live at 03:10 PDT;
  worker deployment `dep-dajsd46ojv1c73f2fa5g` became live at 03:11 PDT.
- Both pre-deploy scripts completed successfully, reported the existing 14
  personal entries and preserved owners. The API runtime directly confirmed
  PostgreSQL 18 on the Render internal host. Worker startup reported all seven
  required variables present, `max_connections=103`, and queue polling with
  concurrency one and depth zero.
- Public `/healthz` and `/readyz` returned HTTP 200 after reopening. Readiness
  reported database, R2, and queue healthy.
- Both services were restarted through Render at 03:19 PDT. The replacement
  API instance confirmed PostgreSQL 18.6 and 14 entries; the replacement worker
  reported all required configuration, the expected connection budget, and
  repeated empty-queue polls. Public health and readiness remained successful.
- The R2 access-key ID did not match the active Cloudflare token. The existing
  secret succeeded with the corrected ID, now saved and deployed through
  `reelbot-shared`; no new credential was created or exposed.
- The daily backup cron used the PostgreSQL 18 client and completed at 03:08
  PDT, writing `backups/2026-09-14.sql.gz`. The production `restore.sh` restored
  that exact object into `reelbot_daily_restore_verify_20260914`, with all 24
  counts and content fingerprints matching the pre-test production snapshot.
- Direct Geocoding and Places API calls from Render succeeded on their first
  diagnostic attempts. No Google credential or restriction change was needed.
- A bounded request to the configured Anthropic fast model succeeded from the
  replacement API instance (13 input tokens, four output tokens).
- An isolated fresh-device share progressed from queued to processing to
  resolved. It produced seven anchored entries and 14 folder associations.
  Its selected Google photo loaded as a 41,785-byte thumbnail, below 60 KB.
  Google imagery remains request-scoped as designed rather than being stored
  in R2. The isolated test libraries were removed after verification.
  This share used the existing extraction cache: it verifies queue consumption
  and entry creation, not a new platform-media download or fresh LLM extraction.
- A generated R2 thumbnail also uploaded and downloaded with identical bytes:
  1,780 bytes, under 60 KB. The small private diagnostic object is retained as
  `thumbs/infra-smoke-20260914.webp`. After test cleanup, all original personal
  tables still matched their pre-test content fingerprints (14 entries, eight
  saves, five folders, one user and one device).
- All 112 backend tests and 41 app tests passed, along with TypeScript checks
  and compilation. The Docker image built successfully and its client reported
  PostgreSQL 18.6.
- A scan of all 42 pre-report commits found no literal Anthropic/Google/GitHub
  credentials, R2 secrets, or non-placeholder remote database passwords. Neither
  `.env` nor `app/.env` was tracked in history; both are ignored. Cookie-file
  ignore rules were broadened. No detected credential was flagged for rotation.

## Remaining acceptance and recovery notes

- A physical iOS share-extension → production-entry UI test was not executed
  during this infrastructure cutover. Backend ingestion and imagery were tested;
  this is not a claim of completed real-phone UI acceptance.
- The checklist's four-to-five additional uncached reels remain an acceptance
  check. The isolated cached-reel smoke test and separate provider probes do
  not establish fresh media fetching or extraction success for those reels.
- Supabase remains intact as the pre-cutover rollback source. After new writes
  enter Render, do not simply switch back: reconcile those writes first.
- Both small scratch restore databases remain available for review, separate
  from the application database. The private pre-cutover R2 checkpoint remains
  available in addition to the scheduled daily backup.
- This remains a single-region deployment with one database instance and one
  worker. R2 backups provide recovery, not automatic database failover.
