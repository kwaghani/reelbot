# ReelBot Render Deployment

This is the recommended backend deployment path for TestFlight. Render gives the iOS app a public HTTPS API URL and also runs the long-lived reel worker.

## What This Creates

`render.yaml` defines three services:

1. `reelbot-api`: public HTTPS FastAPI web service for `/healthz`, `/share`, `/items`, and `/query`.
2. `reelbot-worker`: continuous background worker that processes queued reel ingest/query jobs.
3. `reelbot-scheduler`: cron job that runs `worker/scheduler.py --once` every 3 hours.

The services use `Dockerfile` because reel extraction needs native binaries such as `ffmpeg` and Tesseract.

## 1. Push This Repo To GitHub

Render Blueprints deploy from a Git repo.

```bash
cd ~/Desktop/TEMPNAME
git status
```

If you do not already have a GitHub remote:

```bash
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/reelbot.git
git branch -M main
git push -u origin main
```

If you already have a remote:

```bash
git push
```

Success: GitHub shows `render.yaml`, `Dockerfile`, `api/`, `worker/`, and `db/schema.sql`.

## 2. Confirm Database Schema

In Supabase or your Postgres database, run `db/schema.sql` once.

Success: these tables exist: `groups`, `members`, `items`, `item_saves`, `jobs`, `events`, `outbound_messages`, `nudges`.

Common failure: the worker logs `relation "jobs" does not exist`. Fix: run `db/schema.sql` in the database SQL editor.

## 3. Create The Render Blueprint

1. Go to [Render Dashboard](https://dashboard.render.com/).
2. Click `New +`.
3. Choose `Blueprint`.
4. Connect/select the GitHub repo.
5. Select the branch containing `render.yaml`, usually `main`.
6. Click through to create/sync the Blueprint.

Render will prompt for secret values because `render.yaml` uses `sync: false`. Use the same value for each service when prompted.

Required values:

```text
DATABASE_URL        Your server-side Supabase/Postgres connection string
API_KEY             The same shared secret used by the iOS app
TEST_GROUP_ID       The same UUID used by the iOS app
ANTHROPIC_API_KEY   Your Anthropic API key
GOOGLE_MAPS_API_KEY Your Google Places API key
```

Leave `IG_COOKIES_PATH` blank at first.

Success: Render creates `reelbot-api`, `reelbot-worker`, and `reelbot-scheduler`.

## 4. Wait For Deploys

Open each Render service and watch Logs.

Success signals:

```text
reelbot-api       Uvicorn running
reelbot-worker    Starting worker
reelbot-scheduler Nudge wake complete
```

The first worker start can be slow because Python ML dependencies may download model files.

Common failure: worker runs out of memory. Fix: open `reelbot-worker` Settings and increase the instance type. The Blueprint starts the worker on `standard` because `faster-whisper` and `sentence-transformers` are too heavy for tiny containers.

## 5. Get The API URL

Open the `reelbot-api` service in Render. Copy its public URL. It will look like:

```text
https://reelbot-api.onrender.com
```

If Render adds a suffix to make it unique, use the exact URL Render shows.

## 6. Smoke Test Render

From your Mac:

```bash
export REELBOT_API_URL=https://YOUR_RENDER_API_URL
export REELBOT_API_KEY=YOUR_API_KEY

curl -i "$REELBOT_API_URL/healthz"
curl -i -H "x-api-key: $REELBOT_API_KEY" "$REELBOT_API_URL/items"
```

Expected:

```text
/healthz -> 200 {"status":"ok"}
/items   -> 200 []
```

Queue one test share:

```bash
curl -i \
  -X POST "$REELBOT_API_URL/share" \
  -H "content-type: application/json" \
  -H "x-api-key: $REELBOT_API_KEY" \
  -d '{"url":"https://www.instagram.com/reel/ABC123/","user_name":"Krish"}'
```

Expected: `202 {"status":"queued"}`.

The worker may later mark that fake/example URL as an error. That is fine. This verifies the app-facing `/share` path queues jobs.

## 7. Point EAS Production At Render

Use the Render URL as the iOS API URL.

```bash
cd ~/Desktop/TEMPNAME/app

npx eas-cli env:create production --name EXPO_PUBLIC_API_URL --value "$REELBOT_API_URL" --visibility plaintext --force --non-interactive
npx eas-cli env:create production --name EXPO_PUBLIC_TEST_GROUP_ID --value "YOUR_TEST_GROUP_ID" --visibility plaintext --force --non-interactive
npx eas-cli env:create production --name EXPO_PUBLIC_APPLE_TEAM_ID --value "FGYPK74RB2" --visibility plaintext --force --non-interactive
npx eas-cli env:create production --name EXPO_PUBLIC_IOS_BUNDLE_IDENTIFIER --value "com.krishwaghani.reelbot" --visibility plaintext --force --non-interactive
npx eas-cli env:create production --name EXPO_PUBLIC_APP_GROUP_IDENTIFIER --value "group.com.krishwaghani.reelbot" --visibility plaintext --force --non-interactive
npx eas-cli env:create production --name EXPO_PUBLIC_API_KEY --value "$REELBOT_API_KEY" --visibility sensitive --force --non-interactive
```

Confirm:

```bash
npx eas-cli env:list production
```

Success: production shows all six `EXPO_PUBLIC_*` variables.

## 8. Rebuild And Submit The App

The installed build cannot be fixed in place because Expo public env values are baked into the app at build time.

```bash
cd ~/Desktop/TEMPNAME/app
npm run build:ios:production
npm run submit:ios
```

Install the new TestFlight build. The `API settings are incomplete for this build` banner should be gone.

## 9. Real Share Test

Run the exact iPhone checklist in `SHARE_TEST.md`.

## Instagram Cookies Later

Instagram may reject some reel downloads without authenticated cookies.

If worker logs mention Instagram cookies:

1. Export fresh Netscape-format Instagram cookies.
2. In Render, open `reelbot-worker`.
3. Add a Secret File named something like `instagram-cookies.txt`.
4. Set `IG_COOKIES_PATH` on `reelbot-worker` to the mounted secret-file path Render shows.
5. Redeploy `reelbot-worker`.

Only the worker needs Instagram cookies. The API and scheduler do not.
