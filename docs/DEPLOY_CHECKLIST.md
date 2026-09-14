# ReelBot production deployment checklist

The existing Render services, database, and backup cron are managed by the
ReelBot Blueprint on `codex/venue-identity`. Blueprint auto-sync is paused:
review and approve a manual sync after editing `render.yaml`. Service code
auto-deploys are enabled. A Git push alone does not apply Blueprint settings.

## Before the deployment finishes

1. Open `reelbot-worker` in Render and confirm its instance type is
   **Standard**. If it is Starter, change it before continuing; ffmpeg frame
   extraction can exhaust Starter memory and restart a job.

2. Open `reelbot-api` and set its pre-deploy command to this exact string:

   ```sh
   ./deploy/render/pre-deploy.sh
   ```

   The script runs the dry-run first, then applies migrations only if it passes.
   Set the identical command on `reelbot-worker` if it is not already set.
   A missing command lets deployment succeed without schema tables, then the
   first API query fails.

3. On both service Environment tabs, confirm the `reelbot-shared` group is
   linked and visibly supplies all seven variables: `DATABASE_URL`,
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
   `GOOGLE_MAPS_API_KEY`, and `ANTHROPIC_API_KEY`. Do not copy values into
   `render.yaml`. Service-level variables override the group: verify that
   neither service still points at the former Supabase database.

4. Confirm the API health check path in the dashboard is `/healthz`. It must
   not be `/readyz`, because readiness intentionally queries dependencies.

5. Confirm the Blueprint-managed `reelbot-backup` cron runs daily at 03:00 UTC
   with `./scripts/backup.sh` and the same `reelbot-shared` group. The supplied
   Docker image provides the PostgreSQL 18 client, matching the Render database.
   An older client cannot dump a newer server. An R2 write failure must fail
   the backup job, not report success for a temporary local file.

## Watch the deployment

6. Watch `reelbot-api` boot logs. A missing setting produces a boot failure
   naming the variable; add or correct that exact name in `reelbot-shared`.

7. Watch the pre-deploy output. On a new database, the dry run should show zero
   ownership buckets, then the migration should create the schema. On the
   transferred database, expect the existing migration markers and preserved
   entries; do not expect an empty database. If it
   fails, do not bypass it—fix the reported SQL error and redeploy.

8. Watch `reelbot-worker` logs. It should start, log its configured pool
   ceiling and queue depth, then poll without repeated restarts. Restarts while
   processing usually mean the worker is still on Starter or its memory limit
   is too low.

## Verify the deployed service

9. Run this from a trusted terminal using a disposable device token and a
   known-good public reel:

   ```sh
   REELBOT_VERIFY_TOKEN=... REELBOT_VERIFY_REEL_URL=... \
   ./scripts/verify_production.sh https://reelbot-api.onrender.com
   ```

   It checks liveness, readiness, a durable share, worker completion,
   coordinates, image availability, and thumbnail size. It exits non-zero on
   the first failed category.

10. Resolve failures as follows:

   - A boot error naming a variable means it is absent or misspelled in the
     environment group.
   - Database-unhealthy readiness usually means `DATABASE_URL` is external
     rather than the Render internal URL, or the database connection ceiling
     is too low.
   - R2-unhealthy readiness usually means the token lacks Object Read & Write,
     or `R2_BUCKET` is not `reelbot-media`.
   - Missing geocoding generally means the wrong Google key is in the group.
     Use the unrestricted Maps Platform API key with Places API (New) and
     Geocoding API enabled—not the other project key.
   - Worker restarts mid-job point first to an incorrect worker tier or OOM.

## After verification passes

11. Confirm the existing 14 entries and private folders survived the database
    transfer. Then test four or five additional reels. Confirm the resulting
    entries have images, coordinates where applicable, and the expected
    private folders. Then restart each service once and confirm the data and
    queue recover unattended.
