# ReelBot share diagnostics: human device test

Use build 28 or newer on a physical iPhone. These tests have **not** been run by the coding agent. Simulator, static and native unit checks cannot establish the two-process hardware share flow.

## Open diagnostics

- [ ] Open ReelBot → Settings. **Expected:** Debug shows **Share diagnostics**; in Release, scroll to the version number.
- [ ] Tap the version number five times, less than three seconds apart. **Expected:** **Share diagnostics** opens in Release.
- [ ] Tap **Run canary test**. **Expected:** success, a timestamp and resolved path; **Container reachable: Yes**. Native and app configuration IDs both read `group.com.krishwaghani.reelbot`. **If it fails:** copy the exact canary error and both IDs before doing other tests.

Diagnostics refreshes every two seconds. `Pending queue` displays local files; `Last drain` displays counts and errors; `Native queue scan` lists parsing failures; `Quarantined files` preserves invalid input with a reason. The attempt log is newest first. An empty log is **not** proof of success. `started` without a terminal outcome suggests interruption; inspect Console for the last event. Source app is `null` because no supported public iOS API exposes it; note the source app yourself.

## Share tests

Use your own accessible video URLs and note each URL and test time. One reel can produce several venue entries; duplicate checks mean no additional save or duplicate venue entries for the same reel.

| Action | Expected result | If it fails, check diagnostics |
|---|---|---|
| Open TikTok's system share sheet for one video. | ReelBot appears in the app row or **More** list. | If absent, no attempt is expected: record build number, item type/source app, and check activation/signing configuration. |
| Select ReelBot in that share sheet. | “Saved on this device” appears and dismisses in under one second on a normal run. | Attempt `saved`, item type, URL, container ID, duration and entry/exit memory. A failure shows readable error text for three seconds or until Close. |
| Open ReelBot after that TikTok share. | A local queued save appears, then extracted entries or a reviewable processing state once online. | Last drain successes, pending files, native scan/quarantine and Settings sync error. `saved` means durable local save, not completed extraction. |
| Select ReelBot from Instagram's system share sheet for one reel. | Local confirmation appears and dismisses in under one second. | Latest attempt: `public.url` or text input, correct URL and `saved`. |
| Open ReelBot after that Instagram share. | The reel appears in Recent; processing can proceed online. | Last drain, remaining pending files and sync status. |
| Select ReelBot from YouTube's system share sheet for one video or Short. | Local confirmation appears and dismisses in under one second. | Latest attempt URL, item type, outcome and elapsed time. |
| Open ReelBot after that YouTube share. | The save appears without needing to paste the URL. | Drain counts, pending files, quarantine and sync error. |
| Force-quit ReelBot using the iPhone app switcher. | ReelBot is no longer running. | Record the time for comparison with the next cold launch. |
| Share a new video to ReelBot while ReelBot remains force-quit. | The extension confirms the local save; it does not need to launch ReelBot. | On the next launch, locate this attempt and its outcome/time. |
| Launch ReelBot after the force-quit share. | The link is committed locally on cold launch before network work. | Last drain / native scan and attempt timestamp. A subsequent automatic sync can replace the latest drain record; **Last drain with items or errors** retains the useful result. |
| Enable airplane mode with Wi-Fi also off. | The phone has no network connection. | Canary should still succeed; local storage does not require internet. |
| Share a copied, previously obtained video link from an app's text share sheet while offline. | Local save confirmation succeeds; processing waits. | Attempt `saved` and nonempty URL. If the source app cannot open its own share sheet offline, record that source-app limitation and use an already-loaded video/text URL. |
| Open ReelBot while still offline. | The save is retained locally; a connection error does not remove it. | Last drain successes, Recent queued save and Settings error. |
| Reconnect the phone to the internet. | A refresh can submit the retained save. | Settings → Sync now, then verify last successful sync and save processing state. |
| Share the same reel again. | No additional saved reel or duplicate venue entries are created. | A new extension attempt may exist; drain succeeds and canonical URL deduplication retains the existing save. |
| Share a TikTok URL whose host is `vm.tiktok.com` or `vt.tiktok.com`. | The shortlink is queued as-is; resolving it happens later in the worker. | Latest attempt must retain the shortlink; no extension network request is expected. |
| Complete 30 consecutive video shares through ReelBot. | Every invocation confirms or shows an explicit failure; no crash or unexplained disappearance. | Last 50 attempt records, entry/exit memory and duration. Count outcomes; `started` without completion needs Console review. |
| Open ReelBot after the 30-share run. | All distinct reels remain represented once; no corrupt item blocks later saves. | Queue count, drain success/failure totals, quarantine records and Recent. |
| Open the share sheet for a photo in Photos. | ReelBot normally stays absent for image-only input. | No attempt is expected when iOS filters the extension out. |
| Select ReelBot if Photos offers it for that photo. | Unsupported image input shows a readable rejection; no crash, no false saved state. | Attempt `failed`, input type, extraction error and no new save. This conditional action is skipped if ReelBot is absent. |

## Read failures in under a minute

1. Open Share diagnostics and compare the two App Group IDs.
2. Run the canary. If it fails, capture its exact error/path.
3. Read the newest attempt: no attempt, `started`, `failed`, or `saved` separates non-launch/interruption/write failure from a later drain issue.
4. Read Last drain, Native queue scan and Quarantined files. Pending files with no successful drain indicate an app-side problem.
5. If the link reached Recent, check Settings sync status and the save's processing/review state; extraction is a later step.

For Console.app, select the connected iPhone, start streaming, and use these search tokens:

`subsystem:com.krishwaghani.reelbot.share category:diagnostics`

For an exported log archive or the `log` utility, the exact predicate is:

`subsystem == "com.krishwaghani.reelbot.share" AND category == "diagnostics"`

Every unified log line contains `container_id=...`. Look for `extension_launched`, `container_resolved`, `url_extracted` / `url_not_found`, `write_attempt`, `write_succeeded` / `write_failed`, `extension_failed`, `extension_dismiss`, `queue_read`, `item_quarantined` and `drain`. Entry and dismiss events include memory bytes; dismiss includes elapsed milliseconds. Local attempt history includes full shared URLs—review it before sharing diagnostic text externally.

## Timing and iOS limits

The success UI intentionally stays for 250 ms; actual presentation, provider loading, disk latency and OS scheduling must be timed on hardware. A provider timeout is five seconds, followed by a visible failure. iOS schedules background refresh at its discretion and may suppress it after force-quit; explicitly launching ReelBot is the reliable cold-start drain test. Production-like ongoing processing still requires an available backend; the current HTTPS development preview requires the Mac services and tunnel to remain running.
