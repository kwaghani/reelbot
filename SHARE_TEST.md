# ReelBot Share Sheet Test

Run this after installing a fresh development, preview, or TestFlight build on a real iPhone.

## Happy Path

1. Open ReelBot once.
2. Enter a display name and tap `Continue`.
3. Open Instagram.
4. Find a reel.
5. Tap Share.
6. Confirm `ReelBot` appears in the iOS share sheet.
7. Tap `ReelBot`.
8. Confirm the extension shows `Sent to ReelBot 👍`.
9. Open ReelBot.
10. Go to the `Saved` tab.
11. Confirm a `Saving...` receipt is visible after the share.
12. Pull to refresh.
13. Confirm the place appears within about 1 minute, after the backend worker processes the reel.

## If ReelBot Does Not Appear

This is usually an activation-rule or native-build issue.

1. Confirm the installed build is a native dev/preview/TestFlight build, not Expo Go.
2. Run `cd app && npx expo prebuild --platform ios --clean`.
3. Confirm `ios/ReelBotShareExtension/Info.plist` contains:
   - `NSExtensionActivationSupportsWebURLWithMaxCount`
   - `NSExtensionActivationSupportsText`
4. Rebuild and reinstall the app.

## If ReelBot Appears But Nothing Saves

This is usually App Group, HTTPS API URL, or API key mismatch.

1. Open ReelBot once before sharing. The main app writes `API_URL`, `API_KEY`, `TEST_GROUP_ID`, and display name into the App Group for the extension.
2. Confirm Apple Developer Portal has `group.com.krishwaghani.reelbot` enabled on both:
   - `com.krishwaghani.reelbot`
   - `com.krishwaghani.reelbot.ShareExtension`
3. Confirm the installed build used an HTTPS API URL, not a LAN HTTP URL:
   - `EXPO_PUBLIC_API_URL=https://YOUR_API_DOMAIN`
4. From the phone, open `https://YOUR_API_DOMAIN/healthz`.
5. From a terminal, confirm the API key works:

```bash
curl -i -H "x-api-key: YOUR_API_KEY" https://YOUR_API_DOMAIN/items
```

6. Confirm EAS production or preview env has the same values as the backend:

```bash
cd app
npx eas-cli env:list --environment production
```

7. Check backend logs while sharing:

```bash
journalctl -u reelbot-api -n 100 --no-pager
journalctl -u reelbot-worker -n 100 --no-pager
```

The share extension only captures the URL and POSTs to `/share`; extraction and saving happen later in the worker.
