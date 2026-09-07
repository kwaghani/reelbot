# ReelBot for iOS

Expo SDK 54 / React Native 0.81.5. The app has Saved, Folders, and Settings screens. It works without an account: SQLite holds the library and durable outbox, while Keychain holds a random anonymous device secret. Apple sign-in is available only as an optional sync step.

Run `npm install`, configure `EXPO_PUBLIC_API_URL` in `.env`, and run `npm run prebuild:ios`. Prebuild generates the main target and extension, then `plugins/withPersonalQueue.js` replaces the extension runtime with the native queue writer in `native/`. Run `pod install` in the generated `ios/` directory and `npm run ios` to launch. Use `ReelBot.xcworkspace`; the canonical project is `ReelBot.xcodeproj`.

The shared container identifier is derived from the bundle identifier and remains `group.com.krishwaghani.reelbot`. Both targets must be signed with that entitlement. Build through Xcode with Apple Development signing for simulator container access; manually applying the full device entitlements to a simulator executable causes launch rejection. The queue writer uses individual UUID files and atomic writes, with file protection available after the first device unlock. Main-app acknowledgement happens only after SQLite commits.

The main app drains on launch, foreground entry, connectivity changes, foreground polling, and OS-granted background execution. iOS schedules background tasks; it does not promise immediate execution after force-quitting the main app. The extension can still queue a link while the main app is stopped.

`npm test` exercises real SQLite persistence through the application data layer, concurrency, retry-safe acknowledgement, URL validation, API failures, and Release gating. `npm run typecheck` validates TypeScript. Release verification still requires the built native configuration and a signed physical-device pass.

Optional experimental views are discovered only behind a Debug gate that also checks a native compiled flag. Deleting an experimental view directory does not change the personal library imports or processing path.
