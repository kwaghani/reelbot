# ReelBot for iOS

Expo SDK 54 / React Native 0.81.5. Recent is the default tab, with registry-driven cards, search, filters, folders, detail fields and editing. Map shows linked places and requests optional location only on first visit. Settings contains optional Apple sync, permissions, notifications, JSON export, deletion, storage/version, and Debug-only costs. Release has three tabs; Debug has an additional isolated interest view.

SQLite in the app's Documents directory holds the library and durable outbox. Keychain holds an anonymous device secret. No account or location permission is required for the local save loop. Sync supplies the current taxonomy; app configuration bundles `../config/content_types.yaml` for the first offline launch. Adding a category at the service requires no app rebuild.

Install dependencies, configure `EXPO_PUBLIC_API_URL`, and run `npm run prebuild:ios`. Always use this clean prebuild script: applying the upstream extension plugin repeatedly to an existing generated project can duplicate targets. Native queue templates live in `native/`, wired by `plugins/withPersonalQueue.js`. Use the generated `ReelBot.xcworkspace`; generated iOS files are not tracked.

Both app and extension require `group.com.krishwaghani.reelbot`. Use Apple Development signing for simulator shared-container access. The extension writes individual UUID files atomically, with protection available after the first unlock. The app acknowledges files only after SQLite commits. Back up both Documents and Library when preserving an installed app's data.

The app drains on launch, foreground entry, connectivity changes, foreground polling and OS-granted background execution. iOS schedules background tasks and does not guarantee immediate execution after the main app is force-quit. The extension still queues while the main app is stopped.

`npm test` uses real SQLite for durability, concurrent saves, interrupted acknowledgement, offline resets, editing and review semantics. Pure map tests cover coordinate validity, distance sorting, radius filtering, clusters and date-line bounds. `npm run typecheck` checks TypeScript. Physical-host share latency and traffic attribution require a connected device; unit tests do not replace them.
