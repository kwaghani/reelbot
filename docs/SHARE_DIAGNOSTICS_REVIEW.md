# Share extension diagnostics and static review

September 9, 2026 — build 28, installed on the connected iPhone 15 Pro. This pass implements static correctness and human-readable evidence. **It does not verify the native share flow on hardware.**

## App Group audit

The initial table was reported before edits. No identifier mismatch was found. All configured/effective values are byte-identical: **`group.com.krishwaghani.reelbot`**.

| Location | Verbatim configured value or reference | Effective value |
|---|---|---|
| `app/ios/ReelBot/ReelBot.entitlements` → `com.apple.security.application-groups[0]` | `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBotShareExtension/ReelBotShareExtension.entitlements` → same key | `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBot/Info.plist` → `AppGroup` | `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBot/Info.plist` → `AppGroupIdentifier` | `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBotShareExtension/Info.plist` → `AppGroup` | `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBotShareExtension/Info.plist` → `AppGroupIdentifier` | `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/app.config.ts` | `` `group.${bundleIdentifier}` ``; default bundle `com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/src/config.ts` | `String(extra.appGroupIdentifier || 'group.com.krishwaghani.reelbot')` | `group.com.krishwaghani.reelbot` |
| `app/.env` | `EXPO_PUBLIC_IOS_BUNDLE_IDENTIFIER=com.krishwaghani.reelbot`; no separate group variable | `group.com.krishwaghani.reelbot` (derived) |
| `app/.env.example` | `EXPO_PUBLIC_IOS_BUNDLE_IDENTIFIER=com.krishwaghani.reelbot`; no separate group variable | `group.com.krishwaghani.reelbot` (derived) |
| `app/native/ShareDiagnostics.swift` | Info.plist `AppGroupIdentifier`, fallback `group.com.krishwaghani.reelbot` | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBot/ShareDiagnostics.swift` | Identical generated copy of the preceding constant | `group.com.krishwaghani.reelbot` |
| `app/ios/ReelBotShareExtension/ShareDiagnostics.swift` | Identical generated copy of the preceding constant | `group.com.krishwaghani.reelbot` |
| `app/native/QueueStore.swift` and both generated copies | `ShareDiagnostics.container()`; previously contained the same literal fallback directly | `group.com.krishwaghani.reelbot` |
| `app/src/sharedGroup.ts` | `appConfig.appGroupIdentifier` for preferences; native bridge for queue/diagnostics | Same configured ID; diagnostics displays the native runtime ID separately |
| `app/plugins/withPersonalQueue.js` | `config.extra.appGroupIdentifier` | `group.com.krishwaghani.reelbot` |
| Signed app and extension in `ReelBot-28.app` | App Group entitlements and Info.plist values inspected after signing | `group.com.krishwaghani.reelbot` |

`app.config.ts` supplies the derived value to the app entitlements, app Info.plist, Expo extras, and extension plugin. No independent environment override was introduced. The diagnostic view detects a future native/configuration mismatch rather than assuming they agree.

## Configuration and duplicate projects

- Both targets already had App Group entitlements, but neither had explicit Xcode capability metadata. Added `com.apple.ApplicationGroups.iOS` with `enabled = 1` to both targets and the prebuild plugin.
- Bundle identifiers remain `com.krishwaghani.reelbot` and `com.krishwaghani.reelbot.ShareExtension`; the extension is a child of the app.
- Both app/extension Debug and Release deployment targets are **iOS 15.1**. The prebuild plugin now keeps these matched.
- The extension was already embedded in destination 13, through a phase called `Copy Files`. Renamed it **Embed App Extensions**, preserving the embedded `.appex`.
- Activation remains `NSExtensionActivationSupportsWebURLWithMaxCount = 1` and `NSExtensionActivationSupportsText = true`. No `TRUEPREDICATE`. The native reader explicitly checks **`public.url`** and **`public.plain-text`**, trying text if URL loading/extraction fails. Image-only input is filtered out or visibly rejected if a host nevertheless delivers it.
- Canonical project: **`app/ios/ReelBot.xcodeproj`**. Canonical workspace: **`app/ios/ReelBot.xcworkspace`**, whose file references point to that project and `Pods/Pods.xcodeproj`. The Podfile names its `ReelBot` / `ReelBotShareExtension` targets; the Expo plugin explicitly opens `ReelBot.xcodeproj`.
- `ReelBot 2.xcodeproj`, `Podfile 2.lock`, and copied duplicate workspaces were already absent. Removed the three unreferenced **empty** directories `Pods 2`, `ReelBot 2`, and `build 2`. Kept `ReelBot.xcodeproj/project.xcworkspace`: it is the normal internal workspace, not a Finder duplicate.
- Updated native/Expo build numbers to **28** and the extension display name to **ReelBot**. The app’s generated Info.plist was still at build 25 before this task; the earlier installed release was 27. The new generated and signed outputs now agree.

## Evidence implemented

The native Swift extension writes a `started` attempt before loading input, then updates that attempt to `saved` or `failed`. Interrupted runs retain `started`. Each record includes timestamp, item type, URL or null, outcome, error or null, duration, actual container identifier, entry/exit memory footprint and an attempt ID. `source_app` is explicitly **null** because no supported public iOS extension API provides the host app’s identity; it is not fabricated from the URL’s domain.

`share-attempts.json` holds the newest **50 attempts**, using atomic replacement and a process-shared file lock. Lock waiting is capped at 100 ms. Damaged diagnostic JSON is preserved separately; a new history can then be written. A missing/unreadable container produces a visible error and unified log; naturally it cannot persist a record into an inaccessible container. The screen explicitly explains this absence.

Success displays **Saved on this device** for 250 ms before dismissing. Extraction/loading/write failures display a readable error for three seconds or until Close. A five-second provider-loading watchdog produces a failure trace and error UI. Actual sub-second success latency remains a human hardware measurement.

All unified logs use subsystem **`com.krishwaghani.reelbot.share`**, category **`diagnostics`**, and include the actual `container_id` on every line. Events cover launch/input types, URL extraction/failure, container resolution, write attempt/result, memory at entry/exit, elapsed launch-to-dismiss time, quarantine and drain counts. Full URLs stay in the local attempt file; URL-extracted Console messages report the host.

Console.app search tokens:

```
subsystem:com.krishwaghani.reelbot.share category:diagnostics
```

Exact predicate for a log archive / the `log` utility:

```
subsystem == "com.krishwaghani.reelbot.share" AND category == "diagnostics"
```

## Diagnostics and drain behavior

Settings shows **Share diagnostics** in Debug. In Release, five version-number taps less than three seconds apart open it. It refreshes every two seconds and displays:

- Native runtime App Group ID, app-config ID, reachability, resolved path and exact errors.
- Canary write/read test with unique timestamped value and exact failure reporting.
- Raw pending files, timestamps, count, and manual drain results.
- Latest drain, latest native queue scan, and a retained last drain with items/errors so empty automatic refreshes do not overwrite useful evidence.
- Quarantined original files with diagnostic reasons.
- Newest-first attempt history, distinguishing no attempts from an unreadable container/log.
- Native version, build, Debug/Release configuration and `FeatureFlags.groupsPlaceholder` value.

Cold launch explicitly drains **before** network/identity work. Foreground and background refresh call sync, whose first step is the drain. Concurrent drains share one run. SQLite commits precede queue acknowledgement; retry after interruption deduplicates canonical reel URLs. One failing item does not prevent later valid items from committing. Corrupt, partial, oversized and invalid-name JSON files are moved to quarantine; unsupported URLs are also quarantined. Native scan failures are included in final drain counts. Atomic-write temporary files are not interpreted as completed queue items and remain visible in the raw inspection.

The extension initializes **no React Native bridge**. Its target includes only the native controller, URL parser, queue and diagnostics helpers. Its Podfile target has no Pods; the signed binary has no React/Hermes/Pods dependency. It performs no network calls or image loading. JSON work is limited to bounded local queue/diagnostic records required by this task; it does not parse provider responses or media. This substantially reduces the bridge/media memory risk, but does not establish the device’s actual memory peak or absence of OS kills.

## Validation and delivery

- **41 app tests pass**, including cold launch, interrupted acknowledgements, canonical URL idempotency, concurrent drain coalescing and invalid-link quarantine.
- **92 backend tests pass** against disposable loopback test databases. Fixed the existing test cleanup to clear `natural_geocode_cache`; it previously leaked cache state across test runs. No production data/cache was cleared.
- TypeScript validation passes.
- Native macOS unit checks pass: partial/corrupt/oversized-file quarantine, continued reading, idempotent acknowledgement, canary success/error, 50-attempt retention, concurrent log writers, URL/text support and rejection.
- Signed **Release / iphoneos / generic physical-device** build succeeds with team **FGYPK74RB2**. Both signed targets have build 28, iOS 15.1 and the matching App Group entitlement. No manual signing step was needed for this connected phone. Another team/device requires matching app IDs, the same App Group membership, and valid profiles in Xcode.
- Cloud-backed workspace reads intermittently stalled. Compilation used the local copy of the canonical workspace at `/Users/krishwaghani/Library/Caches/ReelBot/imagery-build/app/ios/ReelBot.xcworkspace`; both project configurations and every generated native source were audited, and changed JS/config source hashes were compared to the repository.

Evidence lives in `share-diagnostics-evidence/`: `static-audit.json`, `build-copy-audit.json`, `source-hashes.json`, `build.json`, `device-build.log`, `extension-linked-libraries.txt`, `app-tests.log`, `backend-tests.log`, `typecheck.log`, and `native-tests.log`. The reproducible static check is `scripts/audit_share_configuration.py`.

## Still requires a human on hardware

The checklist is [DEVICE_TEST.md](</Users/krishwaghani/Desktop/Startups/Apps Built/TEMPNAME/docs/DEVICE_TEST.md>). Not verified in this pass: source-app activation on TikTok/Instagram/YouTube/Photos; actual native queue delivery between the two processes; visible confirmation timing under one second; runtime canary on the phone; force-quit/offline/reconnect behavior; duplicate sharing from actual hosts; 30 consecutive shares; true iOS memory footprint/kill behavior; OS-scheduled background refresh; and complete share-to-extraction results.

The installed app still uses the existing temporary HTTPS development preview. Ongoing online processing requires the Mac service, worker, database and tunnel. Diagnostics, local queuing and canary testing do not require that backend.

[Static audit](</Users/krishwaghani/Desktop/Startups/Apps Built/TEMPNAME/share-diagnostics-evidence/static-audit.json>) · [Build and signing evidence](</Users/krishwaghani/Desktop/Startups/Apps Built/TEMPNAME/share-diagnostics-evidence/build.json>) · [App tests](</Users/krishwaghani/Desktop/Startups/Apps Built/TEMPNAME/share-diagnostics-evidence/app-tests.log>) · [Backend tests](</Users/krishwaghani/Desktop/Startups/Apps Built/TEMPNAME/share-diagnostics-evidence/backend-tests.log>) · [Native tests](</Users/krishwaghani/Desktop/Startups/Apps Built/TEMPNAME/share-diagnostics-evidence/native-tests.log>)
