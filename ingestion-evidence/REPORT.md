# ReelBot ingestion fix — executed evidence

Executed September 7, 2026 (America/Los_Angeles). Branch `codex/ingestion-deep-fix`.

The exact BCD shortlink now works end to end. Fetching, canonical identity, bounded fallbacks, honest failure states, private manual recovery, and inline place choices are implemented. **The full acceptance gate is not met:** automatic geocode coverage is below 85%. Build 21 is signed and packaged; the iPhone was unavailable and the new build has not been installed.

## Acceptance results

| Requirement | Measured result | Outcome |
|---|---|---|
| 30 real reels, ≥15 shortlinks | 30: 16 TikTok shortlinks, 6 Instagram reels, 8 YouTube videos originally published as Shorts | Pass |
| Fetch sufficiency ≥90% | 30/30, 100% | Pass |
| Venue extraction precision ≥85% | 20/21, 95.2%, grounded in captured captions / matched post POI | Pass within this source review; no full-video recall claim |
| Geocode success ≥85% | 14/21 automatic anchors (66.7%); 13/21 after excluding the unsupported Zippy candidate | **Fail** |
| Zero caption → no-content errors | 0/30; all 30 had a captured caption | Pass |
| needs_review under 20% | 5/30, 16.7% of saves; 8/21 venue entries carry review | Pass by save denominator |

`venue-labels.json` records direct source review separately from classifier output. This evaluates whether extracted venue identities are supported by the text / POI actually retrieved; the videos were not independently watched to certify exhaustive venue recall or exact branch accuracy. In particular, Vee’s Cafe has more than one plausible Los Angeles branch. The 14/21 figure measures accepted coordinates, not an independently certified correct-address rate.

Six rejected venues are beyond the required 20 km Los Angeles centroid: Karachi BBQ Tonight, Point Dume, Los Leones Trail, El Matador State Beach, Point Mugu State Beach, and Fitoor. Hey Binge → Binge falls below the name threshold. The Zippy prediction is unsupported as a venue and is retained at low confidence for review. The radius and similarity rules were kept in force, including for older cached places.

## Fixture eligibility and run conditions

The run used a fresh isolated loopback database and two worker consumers, live public fetches, the configured Anthropic classifier, and live Google Places. No captions, outputs or scores were mocked in the 30-reel run. Provider prompt caching was already warm from development; fetch/extraction/place caches in this database started empty. Proxy was off; no Instagram oEmbed token was configured. The deterministic cache and fault-injection tests are separate from this run.

Two discovered links were objectively ineligible as reels and were replaced before the final run: `ZS5pYVR4C` redirected to a photo carousel and `ZSLDh22Yf` redirected to the TikTok homepage. Both failures remain recorded in `evals/ingestion-excluded.json`; they were not counted as successful reels. Final replacements were the Ivy Asia video and a matcha recipe. This is a small convenience/regression sample, not a randomized production reliability estimate.

The final full run was followed by targeted checks for media byte accounting, preservation of legacy entry IDs, platform-specific POI identities, rejection of stale out-of-radius place-cache rows, and filtering of login-page metadata. Those checks do not change the reported 30 fresh-fetch outputs. No metric was recomputed from a selectively rerun successful subset.

## Per-tier results and measured cost

| Final successful tier | Actual requests at tier | Sufficient at tier | Share of all saves | Total provider cost for these saves | Mean per save |
|---|---:|---:|---:|---:|---:|
| Tier 1 | 24 | 23/24 (95.8%) | 76.7% | $0.516310 | $0.022448 |
| Tier 2 | 7 | 7/7 (100.0%) | 23.3% | $0.784467 | $0.112067 |
| Tier 3 | 0 | Not observed | 0% | Not observed | Not measured |

Total: **$1.300776**, mean **$0.043359/save**. There were 29 classifier requests and 38 Google Text Search requests. Costs use returned token usage and observed API call counts multiplied by configured rates ($1/million input tokens, $5/million output, prompt-cache multipliers, $0.032/Places search). They are estimates, not invoices; local CPU, bandwidth and fixed hosting costs are excluded. Per-tier costs above include downstream extraction and geocoding, not just HTTP fetching.

Tier 1 produced 24 readable oEmbed responses; one short YouTube title was insufficient and continued to Tier 2. Six Instagram attempts were explicitly skipped because no token was configured. Tier 2 succeeded on all 7 requests, including one coordinate-bearing Instagram POI. Tier 3 was not needed in this fixture, so its real-world success rate and provider price cannot be claimed. Controlled tests exercised a real generated video, six actual frame extractions, OCR-to-vision fallback routing, music-only audio skipping, byte caps, and immediate cleanup; speech and vision model results in that test were mocked.

Tier HTTP bodies totalled 6,140,916 bytes (55,798 at Tier 1; 6,085,118 at Tier 2). Resolution separately read 3,674,888 bytes. Video download volume was zero for the 30-reel run. Full measurements and query results are in `results-30.json` without full captions or private owner IDs.

## Exact BCD regression

Original: https://vt.tiktok.com/ZSqr2rqry/
Canonical: https://www.tiktok.com/@exploringwithjoy/video/7511087612157873450
Platform ID: `7511087612157873450`. This is also the reported `@exploringwithj...` failure. The existing library contained another alias, `ZSqrYxRch`, for this same video.

Public oEmbed supplied the caption naming BCD Tofu House; `la`, `losangeles`, and `laeats` supplied Los Angeles. The result is **BCD Tofu House, 3575 Wilshire Blvd, Los Angeles, CA 90010**, coordinates **34.06192, -118.3026**. Final full-run confidence: 0.85. Tier log:

```json
[
  {
    "tier": 1,
    "bytes": 2365,
    "outcome": "ok",
    "seconds": 1.297,
    "http_status": 200
  }
]
```

Tiers 2 and 3 did not run for ordinary BCD ingestion. A separate live Tier 2 diagnostic deliberately inspected the same post’s embedded POI: name, Wilshire address, East Los Angeles platform locality, Korean Restaurant category, and no coordinates. It bypassed the classifier and resolved at **0.95 confidence**. `poi-proof.json` records the live HTML tier, POI, route and cost. Its latest repeat uses the global place/city cache; the first diagnostic made two Google calls. Do not confuse this explicit diagnostic with the normal Tier 1-only route.

`cache-proof.json` shows the exact warmed BCD shortlink and canonical URL yielding one owner save, with URL/fetch/extraction/place cache hits, **zero outbound requests and $0 provider cost**. Outbound network/model hooks were configured to fail if invoked. This is a cache invariant test against the actual fetched BCD signals, separate from the live cold benchmark.

## Thirty-reel regression table

“Pass” below means source capture and supported extraction with available anchors; review and unsupported cases are shown explicitly. This does not override the failed aggregate geocode gate.

| # | Shared URL → canonical | Final tier / signals | Extracted entries | Geocode / status | Result |
|---|---|---|---|---|
| 01 | https://vt.tiktok.com/ZSqr2rqry/ → https://www.tiktok.com/@exploringwithjoy/video/7511087612157873450 | T1; caption 109 chars | BCD Tofu House | BCD Tofu House: 3575 Wilshire Blvd, Los Angeles, CA 90010, USA; resolved | Pass |
| 02 | https://vt.tiktok.com/ZSCDMwK3f/ → https://www.tiktok.com/@ornella.munich/video/7597838208012995843 | T1; caption 433 chars | Ornella | Ornella: Platzl 4, 80331 München-Altstadt-Lehel, Germany; resolved | Pass |
| 03 | https://vt.tiktok.com/ZSC9GQCVS/ → https://www.tiktok.com/@hamoni_usa/video/7642836911362444574 | T1; caption 655 chars | Peter Pan Donut & Pastry Shop | Peter Pan Donut & Pastry Shop: 727 Manhattan Ave, Brooklyn, NY 11222, USA; resolved | Pass |
| 04 | https://vt.tiktok.com/ZSM46hvLU/ → https://www.tiktok.com/@quezoncitygovt/video/7478602824494877969 | T1; caption 637 chars | Ramyeon Bar and Restaurant | Ramyeon Bar and Restaurant: 80 Maginhawa, Diliman, Quezon City, 1101 Kalakhang Maynila, Philippines; resolved | Pass |
| 05 | https://vt.tiktok.com/ZSMPLgVYS/ → https://www.tiktok.com/@quezoncitygovt/video/7476319543972121874 | T1; caption 673 chars | Bellini's Italian Restaurant | Bellini's Italian Restaurant: Cubao Expo, 3 General Romulo Ave, Cubao, Quezon City, 1109 Metro Manila, Philippines; resolved | Pass |
| 06 | https://vt.tiktok.com/ZS5GFuKMQ/ → https://www.tiktok.com/@popculturebf/video/7594127498833612052 | T1; caption 273 chars | Hey Binge | Hey Binge: review choices; needs_review | Needs review |
| 07 | https://vt.tiktok.com/ZS6xp1QhX/ → https://www.tiktok.com/@sabrina_parsi/video/7213362829825461509 | T1; caption 237 chars | The Ivy Asia St Pauls | The Ivy Asia St Pauls: 20 New Change, London EC4M 9AG, UK; resolved | Pass |
| 08 | https://vt.tiktok.com/ZSkGhKVwg/ → https://www.tiktok.com/@chefwayneliew/video/7516080346065063186 | T1; caption 160 chars | Zippy | Zippy: 961 Jurong West Street 92, Block 961, Singapore 640961; needs_review | Unsupported venue; review |
| 09 | https://vt.tiktok.com/ZSxo2sX2B/ → https://www.tiktok.com/@ashleymarkletreats/video/7642516492377935135 | T1; caption 927 chars | Viral Dot Cake | Not required; resolved | Pass |
| 10 | https://vt.tiktok.com/ZSxoMaRfh/ → https://www.tiktok.com/@janinemakhlouf/video/7634226382280756498 | T1; caption 186 chars | Greek Yogurt Mochi | Not required; resolved | Pass |
| 11 | https://vt.tiktok.com/ZSxo6FSV2/ → https://www.tiktok.com/@eatpayylove/video/7320725246892412202 | T1; caption 345 chars | Cucumber Salad with Sesame-Ginger Dressing | Not required; resolved | Pass |
| 12 | https://vt.tiktok.com/ZSQep5HyY/ → https://www.tiktok.com/@michaelfinch/video/7609960154339118344 | T1; caption 685 chars | Easy One Pan Dumplings | Not required; resolved | Pass |
| 13 | https://vt.tiktok.com/ZSHa9YNo8/ → https://www.tiktok.com/@james.bok/video/7623534279053610247 | T1; caption 354 chars | Soy Garlic Tofu | Not required; resolved | Pass |
| 14 | https://vt.tiktok.com/ZSayBXayu/ → https://www.tiktok.com/@roa.kurashi/video/7584381792661130504 | T1; caption 500 chars | Lazy Cheesecake with Yogurt and Coconut Sablés | Not required; resolved | Pass |
| 15 | https://vt.tiktok.com/ZSjHvjuvU/ → https://www.tiktok.com/@jmlicup_official/video/7439388529823714567 | T1; caption 71 chars | Spinning Cardio Workout | Not required; resolved | Pass |
| 16 | https://vt.tiktok.com/ZSkpKjwth/ → https://www.tiktok.com/@jeniferliuu/video/7487548198563712263 | T1; caption 396 chars | Perfect Matcha | Not required; resolved | Pass |
| 17 | https://www.instagram.com/reel/DcuTC_5jWDW/ → https://www.instagram.com/reel/DcuTC_5jWDW/ | T2; caption 1767 chars | Trap Queen - Fetty Wap | Not required; resolved | Pass |
| 18 | https://www.instagram.com/reel/DXE43z7DWev/ → https://www.instagram.com/reel/DXE43z7DWev/ | T2; caption 345 chars | Vee's Cafe; Alisa Wine Friends; Karachi BBQ Tonight | Vee's Cafe: 241 S Figueroa St, Los Angeles, CA 90012, USA; Alisa Wine Friends: 1009 Abbot Kinney Blvd, Venice, CA 90291, USA; Karachi BBQ Tonight: review choices; needs_review | Needs review |
| 19 | https://www.instagram.com/reel/DUpDIg1k87g/ → https://www.instagram.com/reel/DUpDIg1k87g/ | T2; caption 569 chars | El Matador State Beach; Point Mugu State Beach; Point Dume; Hollywood Sign; Los Leones Trail | El Matador State Beach: review choices; Point Mugu State Beach: review choices; Point Dume: review choices; Hollywood Sign: Los Angeles, CA 90068, USA; Los Leones Trail: review choices; needs_review | Needs review |
| 20 | https://www.youtube.com/watch?v=fMl1pwvXobk → https://www.youtube.com/watch?v=fMl1pwvXobk | T1; caption 78 chars | Chest Workout with Dumbbell | Not required; resolved | Pass |
| 21 | https://www.youtube.com/watch?v=gB4nd6tVel4 → https://www.youtube.com/watch?v=gB4nd6tVel4 | T2; caption 154 chars | Leg Workout | Not required; resolved | Pass |
| 22 | https://www.youtube.com/watch?v=KyUNiRgraR8 → https://www.youtube.com/watch?v=KyUNiRgraR8 | T1; caption 33 chars | Treino de braços completo | Not required; resolved | Pass |
| 23 | https://www.youtube.com/watch?v=bRWcwVZQXKU → https://www.youtube.com/watch?v=bRWcwVZQXKU | T1; caption 58 chars | Arm Workout | Not required; resolved | Pass |
| 24 | https://www.youtube.com/watch?v=k_xf8RwP9Wo → https://www.youtube.com/watch?v=k_xf8RwP9Wo | T1; caption 43 chars | Pad Thai Recipe (Part 1) | Not required; resolved | Pass |
| 25 | https://www.youtube.com/watch?v=c1QfsmBYakU → https://www.youtube.com/watch?v=c1QfsmBYakU | T1; caption 59 chars | Thai Pork Satay Skewers | Not required; resolved | Pass |
| 26 | https://www.youtube.com/watch?v=dSFU6YFT9J0 → https://www.youtube.com/watch?v=dSFU6YFT9J0 | T1; caption 34 chars | Bruschetta | Not required; resolved | Pass |
| 27 | https://www.youtube.com/watch?v=VTuYlMW3DYQ → https://www.youtube.com/watch?v=VTuYlMW3DYQ | T1; caption 24 chars | Japanese Curry | Not required; resolved | Pass |
| 28 | https://www.instagram.com/reel/DaDPI6muPFN/ → https://www.instagram.com/reel/DaDPI6muPFN/ | T2; caption 842 chars | Bayshack Pizzeria | Bayshack Pizzeria: Shop no.8, Kakad Estate, Dr RG Thadani Marg, B Wing, Siddharth Nagar, Worli, Mumbai, Maharashtra 400018, India; resolved | Pass |
| 29 | https://www.instagram.com/reel/Dbzg5DJIMzU/ → https://www.instagram.com/reel/Dbzg5DJIMzU/ | T2; caption 66 chars; POI | Shivaji Park Dadar West | Shivaji Park Dadar West: platform coordinates; resolved | Pass |
| 30 | https://www.instagram.com/p/DNQqeSYxhGq/ → https://www.instagram.com/reel/DNQqeSYxhGq/ | T2; caption 290 chars | Fitoor; Kateen; Yunomi | Fitoor: review choices; Kateen: 6516 Selma Ave, Los Angeles, CA 90028, USA; Yunomi: 806 E 3rd St #100, Los Angeles, CA 90013, USA; needs_review | Needs review |

Alisa Wine Friends resolved with the **Venice Beach centroid (33.985, -118.4695), 20 km bias**, to **1009 Abbot Kinney Blvd, Venice, CA 90291**. `Vees Cafe`, `Vee’s Café` and possessive variants normalize equivalently; the actual reel produced the 241 S Figueroa St branch, while a separate Venice-biased probe found 5418 W Adams Blvd. The caption alone does not identify one Vee’s branch uniquely.

## Ten additional shortlink checks

Five further TikTok shortlinks in the live fixture and five actual Instagram share links all resolved. The Instagram checks below verify resolution only, not content fetchability or access to a private post.

| Shared URL | Canonical URL | Result |
|---|---|---|
| https://vt.tiktok.com/ZSCDMwK3f/ | https://www.tiktok.com/@ornella.munich/video/7597838208012995843 | Resolved; then Tier 1 fetched |
| https://vt.tiktok.com/ZSC9GQCVS/ | https://www.tiktok.com/@hamoni_usa/video/7642836911362444574 | Resolved; then Tier 1 fetched |
| https://vt.tiktok.com/ZSM46hvLU/ | https://www.tiktok.com/@quezoncitygovt/video/7478602824494877969 | Resolved; then Tier 1 fetched |
| https://vt.tiktok.com/ZSMPLgVYS/ | https://www.tiktok.com/@quezoncitygovt/video/7476319543972121874 | Resolved; then Tier 1 fetched |
| https://vt.tiktok.com/ZS5GFuKMQ/ | https://www.tiktok.com/@popculturebf/video/7594127498833612052 | Resolved; then Tier 1 fetched |
| https://www.instagram.com/share/_a7bsO89b | https://www.instagram.com/reel/DEff-OiJg6-/ | HTTP 302 resolved |
| https://www.instagram.com/share/reel/_gdkGEJBn | https://www.instagram.com/reel/DJvkjAlvNc8/ | HTTP 302 resolved |
| https://www.instagram.com/share/p/BBFVaX2n1Y | https://www.instagram.com/reel/DOBXTYNklfi/ | HTTP 302 resolved |
| https://www.instagram.com/share/BAF0C-s3fM | https://www.instagram.com/reel/DDexAp2RvVT/ | HTTP 302 resolved |
| https://www.instagram.com/share/_o22CKDyw | https://www.instagram.com/reel/DIxIO-ATiiv/ | HTTP 302 resolved |

Instagram share-link discovery provenance is recorded in `shortlink-provenance.json`; each link was read from an existing public issue/comment, not generated. HTTP/meta refresh/static JavaScript redirect variants, loops, private-network redirects, and the empty TikTok author path are separately covered by controlled tests.

## Backfill and preservation

The phone-facing local database and live Supabase database were backed up, restored to disposable databases, migrated, and replay-tested before applying the migration. The new public-source caches have RLS enabled and direct anon/authenticated grants revoked. Supabase advisors reported no new exposed-table error; the existing vector-in-public warning remains. Cache tables intentionally have no client policy because access is through the trusted API.

The local database contained 5 saves and 8 entries. All five identities were backfilled, including its one actual TikTok shortlink. The previous failed BCD save recovered: **1/1 strictly failed saves**. Including two legacy `no_content_found` saves, **2/3 now contain entries**, and **3/3 now have readable captions**. The old DcuTC save correctly reached `extraction_empty`; the independent final benchmark classified the same real caption as media. This classifier variability is not a fetch-empty error and is not counted as a recovered library entry.

| Existing source | Before | After | Entries after |
|---|---|---|---|
| https://www.youtube.com/watch?v=i84Sc5uvQa8 | no_content_found | resolved | 1 |
| https://www.instagram.com/p/DUpDIg1k87g/ | needs_review | needs_review | 5 |
| https://www.instagram.com/reel/DcuTC_5jWDW/ | no_content_found | extraction_empty | 0 |
| https://www.instagram.com/p/DXE43z7DWev/ | needs_review | needs_review | 3 |
| https://vt.tiktok.com/ZSqrYxRch/ | failed | resolved | 1 |

The final library has **10 entries**. All 8 original IDs, owners and note values remain present. The existing notes were empty; preservation of nonempty notes was verified separately in SQL tests. The original custom folder was retained and had no memberships. One duplicate introduced by the backfill was reconciled: the old Los Liones Canyon Trail entry already recorded the exact extracted name “Los Leones Trail” and the same source/city; its provider ID also appeared in the new choices. The original entry ID was kept and its unverified out-of-radius location now requests review. `preservation.json` contains the counts. Exact local reconciliation backup and pre-migration dumps remain outside Git.

The live Supabase database had **0 personal saves/entries**, so its ingestion backfill had **0 to recover**. Its 21 historical ownerless rows remain in the existing orphan audit; no ownership was guessed. Hosted Render services remain suspended. The API and worker serving the local phone/simulator library are running with the updated code. macOS had evicted some dependency files into iCloud; the same pinned Anthropic, yt-dlp, Pillow and pytesseract packages were restored under `~/Library/Caches/ReelBot/ingestion-python`, and the worker uses that local cache through `PYTHONPATH`. No dependency versions were changed.

## Verification and device build

- 49 backend/SQL tests passed, including 429 retries at all four delays, deleted-vs-blocked states, the no-caption assertion, original/canonical deduplication, stale place-cache rejection, owner isolation, manual-entry IDs, verified place choices, legacy entry preservation, media bounds and six-frame cleanup.
- 15 app tests passed; TypeScript checking and Python compilation passed. The durable outbox test covers an offline manual entry followed by a note, including server ID reconciliation.
- The simulator displayed the updated “Is this the right place?” cards and inline choices. The actual Debug long-press gesture was not successfully automated; its callback and owner-only diagnostics endpoint were inspected/tested. Manual-submit and place-choice correctness were verified through API/outbox tests, not claimed as a complete physical-device acceptance pass.
- iOS Release **build 21** succeeded and app/extension code signatures verified. The bundled config points to the phone-facing local API. The Release bundle contains the new recovery copy and excludes the “Save diagnostics” view. The queue-only native share extension remains in the app group.
- Build archive: `/Users/krishwaghani/Desktop/ReelBot-build-21.zip`. The iPhone 15 Pro remained unavailable; **build 21 is not installed**. Existing build 20 was left intact. Installation and real-device share-sheet testing remain pending reconnection.

## Fragile adapters and maintenance

- `worker/fetch/parsing.py`: platform JSON traversal, matching the requested video ID/Instagram shortcode, POI mapping, and access-page markers. It scans JSON scripts rather than relying on one script ID. A real Instagram failure was fixed here: captions from recommended posts must not outrank the requested post.
- `worker/url_resolve.py`: shortlink HTTP/meta/canonical/static-JavaScript adapters and the empty TikTok `@/video/` redirect. It never executes page JavaScript; unsupported scripts fail as link resolution, with a bounded trace.
- `worker/fetch/ladder.py`: sufficiency, tier provenance and honest terminal-state assertions. Increase `FETCH_VERSION` when a parser fix requires refetching cached public signals.
- `config/ingestion.yaml` and `worker/fetch/http.py`: one Chrome header profile, platform rate limits, optional proxy, city mappings, and bounded HTTP. Blocking can change without a code release on the platform side.
- `worker/fetch/media.py`: yt-dlp integration, bounded local media, six-frame OCR/vision and cleanup. Unsupported/blocked streams become recoverable states. Metadata is capped at 5 MB and media at 50 MB; ffmpeg reads local files only. The current 30-reel sample gives no production evidence for this fallback tier.
- `worker/places.py`: normalized name matching, centroid lookup, five-field query mask, strict radius validation, direct platform coordinates and top-three review choices. Improving locality evidence and handle-to-brand interpretation is the next geocoding work; do not weaken the radius to make this benchmark pass.

Operational references: [TikTok embed documentation](https://developers.tiktok.com/doc/embed-videos) and [Google Places Text Search reference](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places/searchText).

## Try the current app

On the simulator, open Recent and inspect the recovered recipe and trail review choices. The updated backend is also available to the existing phone build while the Mac and phone can reach one another. Once the phone reconnects, install build 21 without uninstalling the app, then share the exact BCD shortlink from TikTok and reopen ReelBot to drain the native queue. Verify one BCD save, the Wilshire address, and the existing personal notes/folders. Try an ambiguous trail’s offered choices and a manual topic after an unreadable source. End-to-end physical-device results are intentionally left pending.
