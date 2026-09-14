# ReelBot motion review — build 29

## Initial diagnosis

| Area | Finding before the motion work | Result |
|---|---|---|
| Threading | Recent used RN Animated values with a JS PanResponder for folder gestures and JS work on scroll. | Gesture Handler and Reanimated worklets now own gesture position and header opacity. Viewport bookkeeping is throttled. |
| Layout animation | The old screens mostly changed layout immediately. The primary problem was missing transitions, rather than an established set of expensive size animations. | App-authored animations return opacity and transform only; structural layout commits once before transform reflow. |
| Navigation | Tabs and folder state were rendered directly; no native stack powered folder pushes. | Native stacks preserve the existing routes, with interactive back gestures. Sibling tabs crossfade. |
| Abrupt state changes | Filters, search results, verification, image decode, loading/empty content and detail presentation changed abruptly. | Shared transitions cover these state changes. |
| Dependencies | Reanimated, Gesture Handler, native-stack and the gesture-driven sheet were missing from the motion path. | Reanimated 3.19.1, Gesture Handler 2.28.0, native-stack 7.18.10 and Gorhom 5.2.14 are installed; Babel worklet plugin is last. |

The main cause was immediate state replacement combined with JS gesture/scroll work. Moving animation to the UI thread alone would not have supplied the missing state transitions.

## Shipped behavior

The source of truth is [motion.ts](../app/src/theme/motion.ts): 100/180/260/320/450 ms, enter/exit/move curves, 25 ms card and 20 ms attribute stagger, 60 ms folder delay, and shared springs. Exits use 75% of the matching duration. The native share extension receives the small generated quick-duration mirror in NativeMotion.swift.

Card-to-detail uses a measured frame-origin scale/fade fallback, reversing toward the originating card on close. This ships on the existing React Native architecture without introducing experimental shared-element navigation coupling. It is not a literal shared thumbnail morph. Gorhom supplies finger-tracking, spring settling, two real snap points and pan-down dismissal.

Recent uses a virtualized two-column list (one column at large text sizes). Initial card entrances are capped at eight IDs and are not replayed on recycling. Folder cards mount after the push plus 60 ms. Filters and debounced search retain an outgoing layer for crossfade; the outgoing copy has no scroll ref or active search field. Removal retains the old card briefly for fade/scale, then reflows its neighbors with transforms.

Photos retain a decoded image beneath replacements; new images fade in. Chips crossfade their colors. Shared press feedback and loading labels keep button geometry stable. Haptics are restricted to selection, sheet snaps, committed success and failed actions. The native extension remains a queue-only UIKit process with a trivial confirmation fade.

Reduce Motion is read from AccessibilityInfo and subscribed to once for the app. Spatial transitions and overshoot are replaced with opacity changes; map position commits instantly in this accessibility mode. Diagnostics refreshes remain static.

## Verification

- TypeScript: pass. Static scan: zero `useNativeDriver: false`, raw native Pressable outside the wrapper, TouchableOpacity, RN Animated imports, or hardcoded component animation durations; transform/opacity outputs reviewed manually. [Audit](../motion-evidence/static-audit.json).
- App tests: 41 passed.
- Backend tests: 92 passed (no backend changes required by this motion task).
- Native queue/diagnostic tests: pass.
- Share-extension configuration audit: pass after native project regeneration.
- Physical device: iPhone 15 Pro, iOS 26.6, Release build with dedicated MOTION_PROFILE flag, 240 in-memory private test entries. The fixture is never persisted or synchronized.
- Final phone suite: 8 passed, including actual Reduce Motion and 240-entry fast scroll.

## Measurement limits and remaining acceptance gates

The 240-entry fixture uses kind placeholders; it does not model 240 independently decoded remote venue photographs. Verification acceptance in the fixture simulates a successful local response. This exercise does not establish network ingestion or image-provider performance.

The first Reanimated frame callback sampler was invalid during scrolling: duplicate/non-monotonic callbacks produced negative intervals and impossible frame rates above the display maximum. Those readings are excluded from conclusions. The final sampler uses native CADisplayLink timestamps in the common run-loop mode. It measures main-thread display-link pacing, not GPU rendering or confirmed display presentation. Sampling itself requests 120 Hz during each window. The first interval begins after the first display callback; work before the sampler starts, including JS-to-native dispatch delay, is not captured. It is compiled out of the normal Release build.

The Animation Hitches Instruments capture returned a malformed transferred trace with no render rows. Its failure log is retained; it is not evidence of a clean GPU trace. XCTest recordings are exported unchanged, but moving-frame cadence is approximately 30 fps. Container nominal rate metadata is not the actual capture cadence. Full 60/120 fps screen recordings are therefore still an unmet requirement.

Strict sustained 60/120 fps and zero dropped frames cannot be declared from the initial runs, which had brief search/filter/navigation stalls. The final measurements below document the remaining gaps; functional test success is not a performance pass. A valid Instruments rendering trace and a full-rate capture remain required before signing off those performance gates. Exhaustive interruption, 44-point effective hit-area and image-heavy long-scroll measurements are not established by the automated suite.

## Final hardware measurements

Each transition window is approximately 580 ms; each fast-scroll window is approximately 2 seconds. The table uses the worst maximum interval for repeated non-reduced-motion actions. Full raw intervals and all runs are in [profiles](../motion-evidence/profiles/summary.csv).

| Action | Mean callbacks/s | p95 interval (ms) | Worst interval (ms) | Intervals >16.7 ms |
|---|---:|---:|---:|---:|
| card-detail | 120.0 | 8.34 | 8.34 | 0 |
| marker-sheet | 120.0 | 8.34 | 8.34 | 0 |
| folder-push | 109.7 | 14.49 | 27.18 | 2 |
| filter-change | 114.8 | 8.34 | 25.53 | 1 |
| search-open | 90.8 | 25.57 | 90.40 | 3 |
| search-results | 111.4 | 8.34 | 33.94 | 2 |
| verification-accept | 118.3 | 8.34 | 18.65 | 1 |
| grid-scroll | 120.0 | 8.34 | 8.34 | 0 |

Card/detail, marker/sheet and the eight scroll windows paced at approximately 120 callbacks/s with no interval over 16.7 ms. That is encouraging main-thread evidence, not a verified GPU zero-drop guarantee. Search opening still had a 90.40 ms interval; folder push, filtering, search updates and verification also exceeded the 16.7 ms budget. The strict sustained-frame-rate gate remains **not met**. Native view/keyboard creation and React state commits remain candidates for the brief stalls; a valid Instruments trace is needed to attribute their exact cause.

Actual Reduce Motion was enabled in iOS Settings, its value asserted, and the original setting restored. Reduced card opening and Map/Recent switches paced at 120 callbacks/s, but Settings and sheet dismissal still showed isolated 50.01/73.59 ms intervals. This mode is not exempt from the remaining performance work.

## Original phone recordings

These are unmodified XCTest exports at approximately 30 fps while moving, with hashes and cadence measurements alongside them. They show behavior and gestures, not 60/120 fps delivery.

- [Tab switches](../motion-evidence/recordings/testA_TabSwitch.mp4)
- [Card to detail, sheet drag and close](../motion-evidence/recordings/testB_CardDetailAndDrag.mp4)
- [Folder push and interactive back](../motion-evidence/recordings/testC_FolderPushBack.mp4)
- [Filter and debounced search](../motion-evidence/recordings/testD_FilterAndSearch.mp4)
- [Cluster expansion, marker to sheet](../motion-evidence/recordings/testE_MapMarkerSheet.mp4)
- [Verification acceptance](../motion-evidence/recordings/testF_VerificationAccept.mp4)
- [240-entry fast scroll and active-tab scroll to top](../motion-evidence/recordings/testG_LargeLibraryScroll.mp4)
- [Actual system Reduce Motion](../motion-evidence/recordings/testReducedMotionOnDevice.mp4)

All eight cases passed in the final hardware run. Earlier failures exposed a tab crossfade sizing bug, sheet accessibility grouping, and a retained outgoing list taking the active scroll ref. All three were fixed before this run. Test expectations were corrected to use the fixture's actual venue and an asserted system toggle; the final map case requires both cluster expansion and a real individual marker selection.

## Delivery and follow-up

Build 29 retains the private library and native shared-container queue. The ordinary Release build has no enabled fixture or profiler. It was installed and tested: the existing library opened, Sync now reported “Synced just now,” five rapid version taps opened Share diagnostics, and the shared container reported reachable. That final Release test passed; [screenshots and log](../motion-evidence/release-check/phone-test.log) are retained. The test returned the app to Recent. The local library summary after sync contains 8 entries from 6 saves, zero pending outbound changes, no sync error and zero fixture entries.

The previous temporary HTTPS preview address expired from inactivity. A fresh authenticated-device-only preview connection was started for the final normal-library sync check. This remains a temporary development service running on this Mac, not a permanent hosted backend; a stable hosted endpoint is still needed for dependable daily use away from this development session.

Remaining acceptance work: isolate and reduce the recorded stalls; profile an image-heavy 200+ entry library with a valid rendering trace; supply full-rate 60/120 fps recordings; exhaustively verify interrupted transitions, effective small-control touch targets and every accessibility route. Functional checks passing do not close these remaining gates.
