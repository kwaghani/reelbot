# Venue photos and map sheet review — September 11, 2026

## Diagnosis before changes

The baseline table was reported before edits and is preserved in `photo-peek-evidence/BASELINE.md`, with redacted request-level evidence in `baseline.json`. Google Place Details and photo responses were successful for the five resolved places. The common failure was the expired phone preview address, compounded by the client silently retaining a failed first attempt. There is no evidence of a Google key permission or endpoint-version failure. Historic image decoder failures cannot be reconstructed.

## Photos and backfill

The separate 30-venue live fixture returned **29 Google images and one fallback (96.7%)**. The unresolved fixture was Alisa Wine Friends; this ambiguous fixture query is separate from the user's already resolved entry. No responses or photos were fabricated. See `photo-peek-evidence/30-place-fixture.json`.

The September 11 backfill covered all seven current place entries: four Google photos, BCD's retained reel-cover choice, and two unresolved entries with no eligible image (Karachi BBQ Tonight LA and the Hudson Street address). All five resolved entries already returned backend photos at baseline, so **zero newly recovered backend sources** is the honest count. Restoring the client connection is what makes those images visible again. The earlier eight-row baseline included the subsequently retired, untouched Unidentified-place stub. See `backfill-sep11.json`.

Photo acquisition has a unique durable job per resolved place, independent of entry creation, with retry leases/backoff and an owner-authorized Retry image action. Source, acquisition time and failures are stored and visible in Image diagnostics. Forced Google-media failure tests verify that the failure reason and subsequent site fallback are logged. Place-resolution requests still exclude photos; separate imagery requests use `id,photos`.

The ladder is Google → website image → Commons for eligible kinds → eligible reel cover → map/name fallback, while retaining explicit owner image choices. Google photo bytes and references are not persisted in the shared database. Apple-map views use compatible imagery. Static map snapshots use native MapKit, a street view and one colored marker; the cache is keyed by place/coordinates/color. Uploaded map snippets are owner-isolated even for a shared global place. A name in the display font replaces the former repeated glyph if no snapshot is available.

## Map behavior and review

The map uses a gesture sheet at 190pt, 55% and 92% of its available map pane, with damping 20/stiffness 240. Peek has a thumbnail, name, locality/distance and Directions/Open. Attributes and the gallery mount on expansion. Medium/full share `EntryDetail` with Recent. Camera movement runs alongside sheet changes and restores after background dismissal. Marker changes keep the same sheet instance and fade its content. Reduced Motion removes position animations and retains fades.

Hardware review exposed a real accessibility issue: the sheet container combined its child controls into one accessible element. Setting the container to `accessible={false}` preserves the separately accessible handle, buttons and detail content. The corrected iPhone 15 Pro tests pass the slow upward drag, full expansion, collapse, and Reduce Motion route. Recent details, folder back navigation and tab changes also passed. Initial failing results are retained separately; they are not counted as passes.

The 240-entry fixture uses geographic clustering. It does not establish performance for 200 simultaneously visible individual markers. Device pacing is measured by CADisplayLink intervals, not GPU presentation duration. XCTest's video is useful for gesture review but is not a 60fps capture. The normal-motion result and remaining phone checks are recorded below.

## Remaining limits

The phone build uses an authenticated temporary HTTPS preview backed by this Mac. It depends on the Mac/service/tunnel remaining available; it is not a permanent hosted backend. The previous localhost.run address also rotated during the September 11 session. Cloudflare Quick Tunnel creation timed out twice, and Serveo rejected the anonymous SSH session. The available localhost.run preview was restored; its rotating address remains a testing limitation. The previously configured Render service returns HTTP 503, “Service Suspended.” No paid service was started or reactivated. A permanent service endpoint remains necessary for reliable testing away from this setup.

Marker switching at peek and medium, backdrop collapse and background dismissal passed the final 16-place hardware test at 14:46 PDT. Full-snap switching, pinch zoom while peeking, and true GPU frame presentation at 200 simultaneously visible pins have not been comprehensively measured. The sheet library’s enableTouchThrough option re-enabled interception after expansion; a custom visual-only animated backdrop fixes this. Map-background taps collapse to peek from medium/full and dismiss at peek. No full performance pass is claimed for those cases.

## Recorded evidence

- `photo-peek-evidence/phone-fixed/`: corrected peek, medium/full, dismissal and system Reduce Motion screenshots.
- `photo-peek-evidence/map-peek-device.mp4`: the successful gesture test. This variable-rate XCTest recording contains 344 frames over 22.838 seconds (15.06 fps on average); it does not verify 60fps presentation.
- `photo-peek-evidence/device-pacing/`: preliminary raw on-device timestamp samples with Reduce Motion enabled.
- `photo-peek-evidence/device-pacing-normal/` and `normal-motion-pacing-summary.json`: September 11 normal-motion samples. The 21:26:08Z slow-drag sample records 240 intervals over 2,000.4ms: average 119.976 updates/s, p95/max 8.335ms, no intervals above 16.7ms, Reduce Motion false, 240 fixture entries. This measures main-thread display-link pacing, not GPU presentation or 200 simultaneously visible pins.
- `photo-peek-evidence/checks/`: 103 backend tests, 41 app tests, 11 focused imagery tests, TypeScript and whitespace verification.

The image retry test's forced Google-media failure is asserted against the actual emitted log, including the rejected Google source and following empty website result. Network operations are mocked only in that failure regression, not in the 30-venue live fixture.

## Latest phone verification

The user enabled XCTest automation and the normal-library test completed Sync now, “Synced just now,” Share diagnostics (“Container reachable: Yes”), and Image diagnostics. Evidence is in `photo-peek-evidence/normal-first/`. An unrelated notification screenshot was excluded from the report evidence.

The slow-drag screenshot in `map-measure-corrected/` confirms the medium sheet, dimmed backdrop, selected marker above the sheet and native street-map thumbnail. That test failed its stale XCUIElement frame assertion after the handle re-rendered; the screenshot and native pacing sample show the drag occurred. The follow-up test queries the new handle and checks its accessible snap value.

Normal-library photo acceptance passed at 14:43 PDT after switching the authenticated preview to Pinggy. The test verified Sync now, both diagnostics screens, BCD’s accessible “Loaded” image state and the Retry image action. The BCD screenshot shows the retained real reel-cover photograph and Google gallery alternatives. The retry endpoint returned 200 and the phone displayed “Image refresh requested.” Its screenshot was excluded because an unrelated notification appeared. The earlier failed runs remain separate evidence of the rotating-address problem.

Additional fixes from phone review: Recent cards no longer overlay an extra kind glyph on the image; the label badge remains. Photo retries resolve entries against the latest library and discard removed queued entries, preventing a stale removed ID from failing an entire image batch. The native main-app build number now follows app configuration, matching the extension and in-app label (30).

An earlier map switching test lost iOS automation authorization when the phone switched to another app. It was stopped, and unrelated phone content was not added to the project evidence. The later uninterrupted switching test passed after fixing backdrop interception. Normal Release build 30 installed successfully at 14:38 PDT on September 11; its executable contains no native motion profiler. Installation evidence: `photo-peek-evidence/checks/normal-release-final-install.json`. It preserves personal SQLite data and the native share queue. All 41 app tests, TypeScript and whitespace checks pass after the latest map changes.

The observed address rotation agrees with the provider's documented [free-domain limitations](https://localhost.run/docs/forever-free/). A permanent reachable backend is required to close the reliability issue. The existing Render service is suspended; this session did not activate a paid service.

The next uninterrupted phone run also failed Sync now. A read-only copy of the local state showed an empty outbox and “The service returned an unreadable response”; the formerly healthy preview address now returned HTTP 503 with an HTML “no tunnel here” body, while SSH remained connected under a newly assigned address. This confirms that merely reinstalling with another localhost.run address is not a durable fix.

That run also exposed server rejection of native map-cache uploads: MapKit generated a 360×270 snapshot, but UIKit's default image-renderer scale enlarged the composited JPEG on this 3× screen. The compositor now explicitly uses scale 1 and a versioned cache key, preserving 360×270 pixels and invalidating old local snapshots. Upload failures now log instead of being swallowed. All five resolved-place map thumbnails were subsequently uploaded from the phone and accepted with HTTP 200; see `photo-peek-evidence/map-cache-upload-check.json`.

## Working test window

The installed normal build now uses an authenticated Pinggy HTTPS preview, started at approximately 14:42 PDT. The tunnel reports a 60-minute lifetime, so this is a test window ending around **15:42 PDT September 11**, subject to the Mac and connection remaining available. It is not a deployment. Anonymous `/sync` returned 401. The final normal phone test passed in 31.8 seconds; screenshots are in `photo-peek-evidence/normal-final/`, including `BCD real photograph.png`. The native 360×270 map-cache fix is included.

The final marker-switching test passed in 22.6 seconds. It selects Tartine then Dayglow while retaining peek, expands to medium, selects The Broad while retaining medium, taps the backdrop to return to peek, and dismisses with a map-background tap. Screenshots: `photo-peek-evidence/marker-switch-final/`. Unmodified XCTest recording: `photo-peek-evidence/marker-switch-device.mp4` (gesture evidence, not a 60fps presentation capture). The regular build is restored after the isolated test; fixture entries are never written to the personal library. All five server-cached map images were read back as 360×270 JPEGs (`map-cache-stored-dimensions.json`).

Final normal build 30 restored at 14:46 PDT with the working preview address and custom backdrop fix; installation receipt: `photo-peek-evidence/checks/final-restored-install.json`.
