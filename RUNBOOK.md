# ReelBot operations

## Production infrastructure

The production API URL is `https://reelbot-api.onrender.com` (confirm the
service URL in Render before publishing an iOS build). Render polls only
`/healthz`; it is deliberately dependency-free. Use `/readyz` only for
diagnostics: it reports Postgres, object storage, and durable queue depth.

Deploy from the configured Render Blueprint. Both services use the shared
`reelbot-shared` environment group and run the idempotent migration as their
pre-deploy command: first `python -m db.migrate --dry-run`, then
`python -m db.migrate`. A missing required runtime variable prevents the
service from starting and names the variable in its boot log. Do not add
credentials to `render.yaml` or an app build.

To inspect an outage, first check `/healthz`, then `/readyz`, then the
`reelbot-api` and `reelbot-worker` logs in Render. A database pool startup
line includes the service ceiling, combined planned ceiling (14), and the
database's `max_connections`. Worker logs include queue depth and RSS at job
start, after download, after frame extraction, and job completion.

Backups run daily as the `reelbot-backup` cron service. To restore, use a
fresh scratch database first:

```sh
./scripts/restore.sh backups/YYYY-MM-DD.sql.gz "$SCRATCH_DATABASE_URL"
```

Compare row counts for every public table between the backup source and the
scratch database before any production restore. The backup script keeps the
latest 30 daily date-addressed dumps. Local development uses `docker compose
up -d postgres`; when R2 is absent, storage uses a logged local temporary
backend rather than pretending a production write succeeded.

After a Render deployment, a human with a disposable device token runs:

```sh
REELBOT_VERIFY_TOKEN=... REELBOT_VERIFY_REEL_URL=... ./scripts/verify_production.sh https://reelbot-api.onrender.com
```

The script checks health, readiness, a durable share, worker completion,
coordinates, image availability, and the thumbnail byte budget. It exits
non-zero on any failed step.

Run API and worker from the same revision against the same migrated database. `/healthz` is a liveness response with no dependency calls; `/readyz` checks database reachability, storage, and queue depth. `/content-types` exposes the public taxonomy. Library routes derive ownership from a bearer device credential; clients cannot choose an owner in request bodies. Apple sign-in validates signature, issuer, audience, expiry and a one-use nonce before merging private libraries.

The API uses trusted server database credentials. Direct client table privileges are revoked and RLS is enabled. Composite foreign keys prevent cross-owner folder links. Keep database and provider credentials out of app builds and source control.

Edit `config/content_types.yaml` to extend the taxonomy. Keep keys stable once real entries use them. A category defines its label/icon, geo policy, facet and typed attributes. Required unknown values become null with a specific review reason; unknown attribute keys are rejected on writes and dropped/logged from extraction. The API syncs the validation catalog before writes. Existing entries are retained if a type is later removed; retiring a type permanently needs a deliberate data migration.

The worker gathers available caption, OCR and transcript evidence independently and makes one extraction call. It never geocodes a `geo: never` type. Required or optional geo types are looked up only when a venue is named. Text Search requests only the five ingestion fields and uses a global cache. Hours/ratings load only when a user opens those details, with a separate 30-day cache.

Each attempt has a hard supervisor deadline. Expired processing leases become visible failures, then retry automatically once after 15 seconds. Manual Retry resets the attempt budget while retaining cumulative known usage. A completed extraction checkpoint is reused while its registry version matches; user-confirmed edits survive replay. Missing or inaccessible source signals remain a visible failure instead of an invented entry. An empty extraction displays Nothing to extract.

Use the entry editor to correct attributes or link an existing place. Geo-enabled entries can look up a supplied venue name/city. Dismissal records verification and removes the inline question. Deleting an entry prunes empty automatic folders; deleting a custom folder preserves entries. Delete-all clears the local library and queues remote deletion; new saves wait until the old account reset finishes. A server error remains visible with pending changes in Settings.

Preserved originals remain in `orphaned_items` until ownership can be established. Never guess an owner or delete originals to make counts appear clean. Store backups outside the repository with restrictive permissions. Use `python -m worker.organize_library --user-id UUID` for folder repair and `python -m worker.reindex_embeddings --user-id UUID --apply` to rebuild search vectors.

Costs are tokens/requests times configured rates, allocated by entry count for mixed-type saves. Local transcription/OCR have zero provider API fees, not zero compute cost. Interrupted requests with unavailable provider usage make totals lower bounds. The Debug uncached equivalent is a counterfactual; actual cache-off measurements are recorded separately in the evaluation evidence.
