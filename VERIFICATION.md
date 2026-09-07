# Repeatable verification and rollout

## Safe local checks

Use a disposable Postgres database with pgvector. Never point these tests at the
configured remote DATABASE_URL. The integration suite refuses non-loopback URLs
or database names without `audit`. External extraction/AI/WhatsApp delivery are
substituted only at their service boundaries in tests; database operations,
authorization, queue state, worker writes and transaction rollback are real.

The audit used a separate Docker container named `reelbot-audit-db`, localhost
port 55439, database `reelbot_audit`, and Supabase Postgres 17.6.1.106. Setup:

```sh
docker run --name reelbot-audit-db \
  -e POSTGRES_PASSWORD=reelbot-local-audit-only -e POSTGRES_DB=reelbot_audit \
  -p 127.0.0.1:55439:5432 -d public.ecr.aws/supabase/postgres:17.6.1.106
docker exec -e PGPASSWORD=reelbot-local-audit-only -i reelbot-audit-db \
  psql -U supabase_admin -d reelbot_audit -v ON_ERROR_STOP=1 < db/schema.sql
docker exec -e PGPASSWORD=reelbot-local-audit-only reelbot-audit-db \
  psql -U supabase_admin -d reelbot_audit \
  -c 'grant usage, create on schema public to postgres; grant all on all tables in schema public to postgres;'
```

Install `requirements-dev.txt`, `listener/package-lock.json` and
`app/package-lock.json` with their respective package managers, then:

```sh
TEST_DATABASE_URL=postgresql://postgres:reelbot-local-audit-only@127.0.0.1:55439/reelbot_audit \
  ./scripts/run_checks.sh
```

The test password above belongs only to the disposable loopback container. The
suite creates synthetic users/groups/jobs in that database and never starts a
WhatsApp connection. Stop/remove only this container after use, when its audit
fixtures are no longer needed.

## Application contract

1. `POST /devices` with the private build key and `{ "user_name": "Test name" }`
   returns `{ "device_id": "...", "token": "..." }`. Persist the token privately.
2. All data requests use `x-api-key` plus `Authorization: Bearer <device token>`.
   Any supplied device_id must equal the authenticated device. UUIDs and display
   names alone never grant membership. The default library is shared by testers.
3. Create a group or join with its six-character code. Reads, imports, query,
   copies and deletion require membership on the server. Ten join attempts per
   device per ten minutes are allowed. Invitation links and account login are
   not implemented; invites carry a code entered in Groups.
4. `POST /share` returns 202 `{ "status": "queued", "job_id": "..." }`.
   Acceptance is not extraction success. `GET /jobs/{job_id}` gives queued,
   processing, done, error or cancelled. Only its sender can poll that job.
   `GET /items` exposes group processing/failure cards and saved records.
5. Use a stable `request_id` on query/import retries after uncertain network
   results. Reusing it for different content gives 409. New explicit import
   attempts can use new IDs after a terminal failure. Active duplicate imports
   are coalesced per sender, group and canonical URL.
6. Queued queries that exceed the API wait return status=processing and job_id,
   with no fabricated final answer. Poll the same job for the actual result.
7. Delete removes an item and its saver rows for the whole selected group and
   cancels associated imports. It does not remove copies in other groups or
   delete the external reel. There is no undo/trash feature.

## Coordinated rollout required

Apply the additive `db/schema.sql` upgrade before deploying the API, worker,
listener and rebuilt iOS client. It adds device sessions, invitation attempt
tracking, job identifiers and nudge delivery linkage. RLS is enabled and client
Data API grants revoked; only trusted server connections access these tables.
The schema was applied twice to the audit DB to verify repeatability.

API-key-only old clients are deliberately rejected. Existing records are
preserved, but the old caller-chosen IDs cannot be securely converted into
credentials. Users receive a new device identity and rejoin private groups by
invite code. If a group's invite is no longer available, a trusted operator must
recover it from the server; never implement an ID-only public recovery endpoint.
There is no account recovery or cross-device session synchronization.

The main app and share extension must have the same App Group entitlement.
Open the rebuilt app and choose a library before sharing. The extension no
longer falls back silently to a different library when session/settings are
missing. Verify on a physical iPhone before release; a simulator cannot prove
Instagram/TikTok share-sheet behavior or real platform playback.

Paid AI/Places calls and real WhatsApp delivery require a separately configured
sandbox/test environment. Do not use production chats or send notifications to
real users to complete these checks. Current configured model IDs must be
available to that provider account; published model documentation alone does
not verify account access. Older nudge rows without a delivery link are excluded
from the impact report because delivery cannot be proved retroactively.

## Native simulator verification

The audit used an isolated iPhone 17 / iOS 26.5 simulator and a Release build with
`EXPO_NO_DOTENV=1`, loopback API URL, local test API key and a dedicated test group
UUID. Paid keys were blank in the local API/worker. Both app repositories must be
included when reviewing/shipping this work; root Git ignores the nested `app/`.

Use Xcode simulator signing with the existing entitlements:
`CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- CODE_SIGNING_REQUIRED=NO`.
A build with signing disabled can launch but lacks simulator App Group containers,
so it does not adequately test extension storage. Signing binaries afterward
alone did not supply the simulator's embedded entitlement mapping. A normal
Xcode-signed simulator build did. Do not enable remote provisioning or substitute
production settings just to run local tests.

The installed `react-native-shared-group-preferences` bridge returns JSON strings
on iOS despite its generic object declaration. `sharedGroup.ts` now decodes both
strings and objects; the client regression suite covers settings and job receipts.
The same App Group ID must be embedded in both native targets.

The final local API (port 8019), disposable database, and audit simulator are left
available for inspection. The query-only worker is stopped; no WhatsApp listener,
scheduler, ingestion daemon, or paid provider service is running for the audit.
The retained database contains only synthetic audit fixtures. To release local
resources, stop the audit API, stop `reelbot-audit-db`, and shut down only the
simulator named ReelBot Audit. Do not remove unrelated containers/simulators.
