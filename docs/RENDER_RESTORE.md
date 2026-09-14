# Render restoration — 2026-09-11

The existing Render API and background worker were suspended and still pointed at the retired group application on `main`. Restoration uses the existing services and existing plans, with the personal application deployed from `codex/render-restore`.

| Resource | Configuration |
| --- | --- |
| API | `reelbot-api`, Oregon, Starter ($7/month), `https://reelbot-api.onrender.com` |
| Worker | `reelbot-worker`, Oregon, Standard ($25/month) |
| Source | `kwaghani/reelbot`, branch `codex/render-restore` |
| API command | `./deploy/render/start-personal-api.sh` |
| Worker command | `./deploy/render/start-personal-worker.sh` |
| Deployment | Manual for both services; Blueprint automatic synchronization disabled |
| Legacy scheduler | Remains suspended; outside the personal application's scope |

During the first failed deployment Render temporarily resumed its last successful image. The live OpenAPI check detected this, and maintenance mode was enabled until the corrected personal deployment was Live. Maintenance mode is now disabled. Future restorations should enable maintenance before resuming an old service.

The startup wrappers check the required personal schema before running. Docker includes only backend runtime directories, and its Python search path is `/app`. The prior `/app/worker:/app` path shadowed the `worker` package and failed startup; commit `729b44797f847e239c3a71d1b7bd14fc430cad9b` corrects it.

The worker's fast extraction model is `claude-haiku-4-5-20251001`, with video download and transcription enabled. Existing database and provider credentials are retained. No secrets or personal-library snapshots were published.

## Data and imagery

Before migration, a private public-schema database backup was created and restored into an isolated rehearsal database. The destination already had the personal schema and no active personal libraries. The compilation/photo-job and shared-image-cache migrations were applied transactionally.

The existing phone owner and device identity were transferred with 7 entries, 6 saves, 5 resolved places, 4 folders, 14 folder memberships, and 6 permitted cached images. Entry IDs, notes, save associations, and device token hashes were preserved. Existing orphaned legacy records were preserved and remain outside the personal API.

`image_assets` now stores bounded, checksum-addressed, non-Google thumbnails so the worker and API can use the same image cache on separate hosts. Google image bytes are not stored there. RLS is enabled; public, anonymous, and authenticated database access is revoked. The API checks ownership before serving referenced images.

Private recovery files are under `~/Library/Caches/ReelBot/render-restore/`, outside Git. Restoring the public-only dump into a fresh database requires the `vector` extension first; the destination's existing `public` schema must be handled in the restore table of contents. Do not restore over an active library without reviewing later changes.

## Validation

- Backend suite: 104 tests passed, including shared image retrieval from an empty second-instance cache and image bounds/RLS checks.
- Backup restore and migration rehearsal passed.
- Personal-library transfer committed; IDs and notes compared exactly.
- iPhone Release build succeeded with the permanent Render API address.
- API and worker: both Live on `729b44797f847e239c3a71d1b7bd14fc430cad9b`.
- Hosted health confirms `personal-reels`; anonymous library reads return 401, cross-owner thumbnail/diagnostic reads return 404, sync and search respond successfully. The isolated probe owner was removed.
- Worker completed all five venue-photo jobs using the configured Google provider.
- Live owner sync returned all 7 entries and 4 folders with exact matching titles and notes. All 5 cached map images were retrieved from the hosted API and decoded as 360×270 JPEGs; searching for an existing place passed. The temporary verification device was removed.
- Permanent-endpoint Release app installed on the iPhone 15 Pro. Final phone check passed at 16:22 PDT on September 11 (32.106 seconds): fresh successful-sync timestamp, shared-container diagnostics reachable, image diagnostics, BCD venue photograph loaded, and image retry action. The first check expected a different success label; it was corrected to require an advancing successful-sync timestamp. Evidence: `/tmp/reelbot-render-phone-final.xcresult`; private retained screenshots: `~/Library/Caches/ReelBot/render-restore/phone-final/`.

Hosting restoration does not itself resolve platforms refusing reel downloads. Real compilation extraction accuracy and provider fetch restrictions remain separate validation work.
