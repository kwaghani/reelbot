# ReelBot iOS App

Minimal Expo iOS client for the existing reel-bot backend. It has a stored display name and server-issued device session, four tabs (Saved, Folders, Groups, Ask), and an iOS share extension for Instagram/TikTok/YouTube links.

## Configure

Copy the app env example and fill the backend values. The iOS identifiers are already Apple-account-ready defaults for this project, but you can change them before registering the identifiers in Apple Developer.

```bash
cd app
cp .env.example .env
```

```bash
EXPO_PUBLIC_API_URL=https://your-vps.example.com
EXPO_PUBLIC_API_KEY=same-value-as-api-key
EXPO_PUBLIC_TEST_GROUP_ID=00000000-0000-0000-0000-000000000000
EXPO_PUBLIC_IOS_BUNDLE_IDENTIFIER=com.krishwaghani.reelbot
EXPO_PUBLIC_APP_GROUP_IDENTIFIER=group.com.krishwaghani.reelbot
EXPO_PUBLIC_APPLE_TEAM_ID=YOUR10CHARTEAMID
```

The build key admits private testers. Each installation also receives a private bearer token, persisted with its device ID and shared with the extension using App Group storage. All library actions require this session. Never send another installation’s device ID to claim its groups. This is device-based access; account recovery and multi-device login are not implemented.

## Apple Setup

Use the Apple developer account to create these identifiers before the first device build:

1. Main app Bundle ID: `com.krishwaghani.reelbot`
2. Share extension Bundle ID: `com.krishwaghani.reelbot.ShareExtension`
3. App Group: `group.com.krishwaghani.reelbot`
4. Enable the App Group capability on both Bundle IDs.
5. Add your 10-character Team ID as `EXPO_PUBLIC_APPLE_TEAM_ID`.

If you choose different identifiers, update `.env` before running prebuild or EAS.

## Build And Run

```bash
cd app
npm install
npx eas login
npm run credentials:ios
npm run build:ios:dev
npx expo start --dev-client
```

Install the generated development build on the iPhone, open it once, enter a display name, then share an Instagram or TikTok link into ReelBot from the iOS share sheet.

Share extensions do not work in Expo Go. The extension only reads the shared URL and App Group settings, posts to `/share`, shows a short confirmation, and returns; the VPS worker still does all extraction and processing.

## Local Native Check

To inspect the generated Xcode project locally:

```bash
npm run prebuild:ios
open ios/ReelBot.xcworkspace
```

In Xcode, confirm both the `ReelBot` target and `ReelBotShareExtension` target use your Apple team and have the same App Group. Commit or keep the generated `ios/` folder only if you want to manage native project files directly; EAS can generate it during cloud builds from `app.config.ts`.

## Reliability update

The backend schema and app must be updated together. Existing installations
receive a new secure device identity and must rejoin private groups by invite;
old records remain. The shared default library is explicitly labelled as shared
with all testers. Choose the active library in Groups before using the extension.
The extension says **Queued**, then Saved shows processing, saved, or failure.
Original playback may require signing into Instagram/TikTok/YouTube separately.
There is no in-app reel detail URL or login-return flow.

Checks: `npm run typecheck` and `npm test`. The tests exercise the actual client
request/session code with simulated network/storage boundaries; they do not
claim to verify native App Group entitlements or external platform playback.
