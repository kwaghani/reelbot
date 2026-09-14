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

## Pending cutover and checks

- Maintenance-window approval; public traffic is not paused yet.
- Recheck the source fingerprint immediately before switching both services.
- Replace each service-level Supabase `DATABASE_URL` with the Render internal
  connection. The shared group already contains the Render URL.
- Verify both new deployments, migrations, worker polling, and API readiness.
- R2's deployed access-key ID did not match the active Cloudflare token. The
  existing secret succeeded with the correct ID. The corrected ID is saved in
  `reelbot-shared` using Save only; services must redeploy to receive it. R2
  upload/download and scratch restore were verified with the corrected pair.
- Deploy the PostgreSQL 18 backup client and trigger the daily backup cron.
- Verify a fresh share through extraction, geocoding, imagery, and the iOS app.

Do not treat a successful database copy as a completed service cutover. Keep
Supabase intact until production verification passes. If any writes reach the
old source after this snapshot, reconcile and reverify them before cutover.
