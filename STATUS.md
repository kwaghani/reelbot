# ReelBot TestFlight Status

## Fixed

- Expo app config is now explicit for iOS/TestFlight: owner, bundle ID, App Group, version, iOS build number, icon, splash, and export-compliance flag.
- Added preview and production EAS build profiles. Production is configured for iOS store/TestFlight distribution.
- Added a coral ReelBot app icon and matching splash assets in `app/assets/`.
- Verified `expo-share-extension` prebuild output creates both the main app and share extension App Group entitlements.
- Added `api/smoke_test.py`, a mock-backed smoke test for `/share`, `/query`, `/items`, API-key rejection, bad share validation, ingest job shape, and query event logging.
- Added deploy-ready systemd units for API, worker, and scheduler.
- Added a Caddy HTTPS reverse proxy config.
- Added `api/.env.example`, refreshed `app/.env.example`, and wrote `RUNBOOK.md`.
- Added a minimal app design system in `app/src/theme.ts` and applied it to Saved, Ask, the tab bar, and the first-launch name prompt.
- Hardened the share extension URL extraction and POST flow, including explicit success/failure feedback and a recent-share receipt shown as `Saving...` in the Saved tab.
- Added `SHARE_TEST.md` with the exact physical-device share-sheet verification flow.

## Decisions

- Kept the product model intentionally simple: one test group, display-name identity, no login, no new backend product logic.
- Kept `com.krishwaghani.reelbot` and `group.com.krishwaghani.reelbot` as the default identifiers because they already match the Apple/EAS setup used during development.
- Used Caddy for HTTPS because it automatically provisions and renews TLS certificates. iOS production/TestFlight builds should use HTTPS, not the LAN HTTP URL used during local debugging.
- Removed generated/vendor output (`node_modules/`, generated `ios/`) from the app git index. EAS should build from source/config plus `package-lock.json`.
- Kept share feedback honest: the extension confirms only that `/share` accepted the URL, while the app shows `Saving...` until the backend worker finishes and `/items` reflects the saved place.

## Verified Locally

- `npm run typecheck` passes in `app/`.
- `npx expo-doctor` passes in `app/`.
- `npx expo prebuild --platform ios --clean` succeeds and installs CocoaPods.
- Generated iOS entitlements contain `group.com.krishwaghani.reelbot` on both `ReelBot` and `ReelBotShareExtension`.
- Generated share extension activation rules support text and one web URL.
- `.venv/bin/python -m compileall api` passes.
- `.venv/bin/python api/smoke_test.py` passes.
- `api.main` imports and loads valid settings with env vars.

## Not Verified

- Physical-device share extension behavior is not verified here. It must be tested on an iPhone because iOS share extensions do not run in Expo Go and have native lifecycle limits.
- Apple Developer portal setup and App Store Connect app record are not verified; they require interactive Apple credentials.
- EAS production build and TestFlight submission are not run here; they require interactive Expo/Apple credentials and App Store Connect access.
- Live database ingest and retrieval are not fully exercised by the smoke test. The smoke test uses mocks to verify API wiring without mutating production/staging data.
- HTTPS domain and Caddy certificate issuance are not verified here; they require the VPS DNS/domain.

## Residual Notes

- `npm audit` reports moderate transitive advisories through the Expo SDK 54 dependency tree. The available forced fix upgrades Expo to SDK 56, while the current `expo-share-extension` version targets SDK 54. I left SDK 54 in place to preserve share-extension compatibility.
- `app/.env` is local-only and ignored. For cloud builds, set EAS environment variables as described in `RUNBOOK.md`.
