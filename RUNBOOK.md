# ReelBot TestFlight Runbook

Follow these steps top to bottom from a clean checkout on the VPS/Mac. Commands assume the repo lives at `/opt/reelbot` on the VPS and `/Users/krishwaghani/Desktop/TEMPNAME` locally; adjust paths if you deploy elsewhere.

## 1. Create Production Secrets

Needs: database URL, Anthropic key, Google Places key, and one generated test group UUID/API key.

```bash
uuidgen
openssl rand -hex 32
```

Put the UUID in `TEST_GROUP_ID` and the random hex in `API_KEY`.

Success: you have one UUID and one long API key.  
Common failure: using different values for backend and app. Fix: copy/paste the exact same `TEST_GROUP_ID` and `API_KEY` into both backend and EAS/app env.

## 2. Deploy Backend Code To VPS

Needs: SSH access to the VPS and Python 3.12.

```bash
sudo mkdir -p /opt/reelbot
sudo chown -R reelbot:reelbot /opt/reelbot
cd /opt/reelbot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r worker/requirements.txt
```

Create `/opt/reelbot/.env` from `api/.env.example`.

Success: `source .venv/bin/activate && python api/smoke_test.py` passes locally with mocks, and the real services can read `/opt/reelbot/.env`.  
Common failure: missing `DATABASE_URL`. Fix: use the server-side Supabase/Postgres connection string, not anon client credentials.

## 3. Install systemd Services

Needs: root on the VPS.

```bash
sudo cp deploy/systemd/reelbot-api.service /etc/systemd/system/
sudo cp deploy/systemd/reelbot-worker.service /etc/systemd/system/
sudo cp deploy/systemd/reelbot-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reelbot-api reelbot-worker reelbot-scheduler
sudo systemctl status reelbot-api reelbot-worker reelbot-scheduler
```

Success: all three services are `active (running)`.  
Common failure: service exits immediately. Fix: run `journalctl -u reelbot-api -n 100 --no-pager` and check missing env vars or Python dependencies.

## 4. Put API Behind HTTPS

Needs: a DNS record such as `api.example.com` pointing to the VPS. iOS TestFlight builds must use HTTPS; plain HTTP is blocked by App Transport Security.

Install Caddy and copy the config:

```bash
sudo apt install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/api.example.com/YOUR_API_DOMAIN/g' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Verify:

```bash
curl -i https://YOUR_API_DOMAIN/healthz
curl -i -H "x-api-key: YOUR_API_KEY" https://YOUR_API_DOMAIN/items
```

Success: `/healthz` returns `401 Invalid API key` without a key, and `/items` returns `200 []` or saved items with the key.  
Common failure: Caddy cannot get a cert. Fix: ensure DNS points to the VPS and ports `80`/`443` are open.

## 5. Configure EAS Project

Needs: Expo account.

```bash
cd app
npx eas-cli login
npx eas-cli init
```

If EAS says the project is already linked, keep the existing project ID in `app.config.ts`.

Success: `npx expo config --json | grep projectId` shows an EAS project ID.  
Common failure: dynamic config cannot be edited. Fix: manually set `extra.eas.projectId` in `app.config.ts`.

## 6. Configure Apple IDs And App Group

Needs: Apple Developer account.

In Apple Developer Portal:

1. Confirm/create App ID: `com.krishwaghani.reelbot`
2. Confirm/create Share Extension App ID: `com.krishwaghani.reelbot.ShareExtension`
3. Confirm/create App Group: `group.com.krishwaghani.reelbot`
4. Enable the App Group capability on both App IDs.

Success: both App IDs list `group.com.krishwaghani.reelbot` under App Groups.  
Common failure: EAS credentials fail for the extension. Fix: App Group capability must be enabled on the extension App ID too.

## 7. Create App Store Connect App

Needs: App Store Connect access.

In App Store Connect:

1. Create a new app.
2. Platform: iOS.
3. Bundle ID: `com.krishwaghani.reelbot`.
4. SKU: any unique internal string, for example `reelbot-ios`.

Success: the app exists in App Store Connect and can receive TestFlight builds.  
Common failure: Bundle ID not shown. Fix: create/refresh the App ID in Apple Developer first.

## 8. Set EAS Environment Variables

Needs: the HTTPS API domain and backend API key/test group.

```bash
cd app
npx eas-cli env:create production --name EXPO_PUBLIC_API_URL --value https://YOUR_API_DOMAIN --visibility plaintext --force
npx eas-cli env:create production --name EXPO_PUBLIC_TEST_GROUP_ID --value YOUR_TEST_GROUP_ID --visibility plaintext --force
npx eas-cli env:create production --name EXPO_PUBLIC_APPLE_TEAM_ID --value FGYPK74RB2 --visibility plaintext --force
npx eas-cli env:create production --name EXPO_PUBLIC_API_KEY --value YOUR_API_KEY --visibility sensitive --force
```

Repeat for `preview` if you want an internal non-TestFlight build:

```bash
npx eas-cli env:create preview --name EXPO_PUBLIC_API_URL --value https://YOUR_API_DOMAIN --visibility plaintext --force
npx eas-cli env:create preview --name EXPO_PUBLIC_TEST_GROUP_ID --value YOUR_TEST_GROUP_ID --visibility plaintext --force
npx eas-cli env:create preview --name EXPO_PUBLIC_APPLE_TEAM_ID --value FGYPK74RB2 --visibility plaintext --force
npx eas-cli env:create preview --name EXPO_PUBLIC_API_KEY --value YOUR_API_KEY --visibility sensitive --force
```

Success: `npx eas-cli env:list --environment production` shows all four variables.  
Common failure: app builds with blank API settings. Fix: set variables on the same EAS environment used by the build profile.

## 9. Build For TestFlight

Needs: EAS project linked and Apple credentials.

```bash
cd app
npm run build:ios:production
```

When prompted, let EAS manage credentials unless you have a specific distribution certificate/profile to upload.

Success: EAS build ends with an `.ipa` and a build URL.  
Common failure: credentials missing for `ReelBotShareExtension`. Fix: run `npm run credentials:ios` and set up credentials for both targets.

## 10. Submit To TestFlight

Needs: App Store Connect API key or Apple login.

```bash
cd app
npm run submit:ios
```

Success: App Store Connect shows the uploaded build under TestFlight after processing.  
Common failure: export compliance prompt appears. Fix: this app uses standard/exempt encryption; `ITSAppUsesNonExemptEncryption=false` is already in config.

## 11. Add Internal Testers

Needs: App Store Connect access.

In App Store Connect:

1. Open the app.
2. Go to TestFlight.
3. Add the processed build to an internal testing group.
4. Fill required test information.
5. Invite testers by email.

Success: testers receive TestFlight invites.  
Common failure: build stuck processing. Fix: wait 10-30 minutes, then refresh App Store Connect.

## 12. Tester Install And Smoke Test

Needs: tester iPhone and TestFlight app.

Tester steps:

1. Install TestFlight from the App Store.
2. Accept the email invite.
3. Install ReelBot.
4. Open ReelBot once and enter a display name.
5. Share an Instagram/TikTok reel into ReelBot.
6. Pull to refresh Saved after the worker processes the job.
7. Ask a grounded question in the Ask tab.

Success: share extension shows the saved confirmation, `/share` queues a job, the worker saves an item, and Saved/Ask reflect backend state.  
Common failure: share extension hangs. Fix: check `https://YOUR_API_DOMAIN/healthz` from the phone, Caddy logs, and `journalctl -u reelbot-api`.
