# Verification — September 7, 2026

Acceptance is **FAIL**. Unexecuted checks are FAIL. The final run uses the ten-category registry and compact response schema identified in `audit-evidence/golden-results.json`.

Run `TEST_DATABASE_URL=<disposable loopback database with test in its name> scripts/run_checks.sh`. Never point this command at a live database.

| Check | Result | Evidence |
|---|---|---|
| 5.1.1 Both exact purge scans return zero hits | **PASS** | Exact requested commands; purge-scans.json. No additional exclusions. |
| 5.1.2 Retired listener directory is absent | **PASS** | source-preconditions.json: listener_exists=false. |
| 5.1.3 Retired environment settings are absent | **PASS** | All root/app/API environment examples and live local files, Render config and both systemd units inspected by key only. |
| 5.1.4 Backend/app checks and native builds succeed | **PASS** | 29 Postgres/backend checks, 12 app checks, TypeScript and Python compilation; Debug/Release simulator and signed Release iPhone build 20 succeeded. Dependency build warnings remain. |
| 5.2.1 Migration dry-run includes all three ownership buckets | **PASS** | migration-preflight.json: 0 single-owner, 0 multi-owner, 21 unresolved originals; restored rehearsal before live application. |
| 5.2.2 Retired database tables are dropped | **PASS** | database-final.json: zero retired tables remaining. |
| 5.2.3 Every original item is accounted for | **PASS** | Live 0 private entries + 21 quarantined originals = 21 originals. Test migration exercises duplication for multiple owners while counting distinct originals. |
| 5.2.4 No arbitrary owner was assigned | **PASS** | All 21 ownerless originals remain quarantined. Existing phone-test database retained all 8 entry IDs/owners/notes/folder links. |
| F.1.1 Ten complete registry schemas | **PASS** | config/content_types.yaml and integration test. Other has only topic; other schemas contain 3–5 attributes. |
| F.1.2 Eleventh category works by changing YAML only, then is removed | **PASS** | Live handmade origami video + OCR classified as Craft; filter/detail/Crafts → Paper observed without rebuild. Source hashes unchanged except YAML. Ten categories restored and test entry removed. |
| F.1.3 Unknown attribute writes rejected | **PASS** | API and actual Postgres trigger tests reject unknown keys; extraction drops/logs them and retains missing-required review reasons. |
| F.2.0 Forty fully hand-labeled real reels with required composition | **FAIL** | Forty real URL attempts cover the nominal 10/6/6/4/4/4/4/2 buckets, but no row has complete independent video labels. Some URLs are unavailable and some short videos are not verified vertical reels. Existing provisional labels omit subjects and contain mismatches. |
| F.2.1 Classification accuracy at least 85% | **FAIL** | Provisional metrics below; incomplete independent labels prevent an acceptance pass regardless of the numeric threshold. |
| F.2.2 Required-attribute precision at least 80% | **FAIL** | Provisional metrics below; incomplete independent labels prevent an acceptance pass regardless of the numeric threshold. |
| F.2.3 Resolved-place precision at least 85% | **FAIL** | Provisional metrics below; incomplete independent labels prevent an acceptance pass regardless of the numeric threshold. |
| F.2.4 Recall at least 75% | **FAIL** | Provisional metrics below; incomplete independent labels prevent an acceptance pass regardless of the numeric threshold. |
| F.2.5 Zero silent drops | **FAIL** | 3 provisional expected entries missing without a failed save. Complete independent labels are still required. |
| F.2.6 Four no-content reels return no_content_found | **PASS** | All four designated empty fixtures returned zero entries and no_content_found. |
| F.2.7 Five-plus listicle produces separate entries | **PASS** | The five-hike reel produced five separate place entries. Another taco list produced five; an Indian recipe compilation produced five recipes. |
| F.2.8 Both mixed fixtures produce two types | **FAIL** | First designated mixed fixture failed processing; second returned no content. A different product fixture did produce product and media entries, which does not substitute for these checks. |
| F.2.9 Review rate below 20% | **PASS** | 5/46 entries = 10.87%. Five unresolved-place reasons and five low-confidence reasons refer to the same five entries. |
| F.3.1 Instagram, TikTok and YouTube native share sheets work | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.3.2 Return under 400 ms in each native host, measured | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.3.3 Backgrounded and force-quit share survival | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.3.4 Airplane-mode queue automatically resolves after reconnect | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.3.5 Fifty consecutive native-host shares without crash | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.3.6 Duplicate native-host share creates one save with its N entries | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.3.7 Zero network calls from the extension process | **FAIL** | Physical iPhone unavailable; required host matrix not executed for build 20. Native queue/SQLite idempotency and 50-write durability tests pass as supporting evidence, not as a replacement for this check. |
| F.4.1 Recent launches first and new save appears within 2 seconds | **FAIL** | Recent default verified after relaunch. Cold-launch visibility latency was not measured. |
| F.4.2 Inline verification appears only when needs_review is true | **PASS** | Controlled Thai recipe and unresolved-cuisine recipe inspected together; only unresolved recipe prompted. Original unresolved-place cards also show specific reasons. |
| F.4.3 No review badge, banner or tab red dot | **PASS** | Recent, Map, Settings and Debug navigation inspected; pending-review counters absent. |
| F.4.4 Dismissal survives restart | **PASS** | Mystery recipe dismissed, app terminated/relaunched; no prompt. Database verified_at persisted and needs_review=false. |
| F.4.5 Map includes every anchored entry and no others | **PASS** | Two actual anchored places plus a controlled product sharing one place rendered; unanchored workout filter had no markers. Coordinate filtering/clustering tests also pass. |
| F.4.6 Every feature works with location denied | **FAIL** | Denied permission and verified saved-place bounding-box fallback. Complete denial sweep across every feature, including account/export/notifications, was not performed. |
| F.4.7 Granted location yields correct ascending Near me distances | **PASS** | Simulated public Santa Monica point: Los Liones 7.3 km, Hollywood Sign 21 km, ascending. Date-line/distance unit tests pass. |
| F.4.8 Registry-specific marker icons differ | **PASS** | Product shopping-bag marker and place pin marker observed using controlled test entry and actual saved places. |
| F.4.9 Release has three tabs and Debug has four | **PASS** | Release simulator observed Recent/Map/Settings; restored Debug observed Recent/Map/Groups/Settings. TestFlight-specific runtime remains untested. |
| F.5.1 Type/facet auto-filing | **PASS** | All 46 final-run entries have type and facet folders. Other intentionally has no facet; unknown required facets remain reviewable rather than inventing a value. |
| F.5.2 Multi-valued facets file into every matching subfolder | **PASS** | Real Postgres and app tests: chest-and-arms entry is in both Chest and Arms. |
| F.5.3 No empty automatic folders | **PASS** | Final-run database query: zero empty automatic folders; pruning tests pass. Empty custom folders are permitted. |
| F.5.4 Deleting custom folder preserves entries | **PASS** | Owner-scoped API integration test deletes folder and retains entry and automatic memberships. |
| F.5.5 Search covers title, summary, attributes, folders and place name | **PASS** | Database search and actual SQLite tests cover these fields, notes, semantic ranking and ownership isolation. |
| F.6.1 Debug reports measured cost per save by content type | **PASS** | Debug UI showed Recipe/Workout/Product/Place/No Entries buckets. Live provider measurements in cost-by-type.json; zero-cost controlled UI fixtures are clearly separated from paid measurements. |
| F.6.2 No geocoding for geo:never across golden set | **PASS** | golden-geo-and-folders.json: zero calls attributed to never types across the final 40-save run; per-save skip/call counters retained. |
| F.6.3 Five different real reels for one venue make one Places call | **FAIL** | Boundary probe with five controlled save fixtures made one real Google lookup and four cache hits. Five distinct real videos naming the same venue were not tested end to end. |
| F.6.4 Ingestion uses exactly the five allowed Places fields | **PASS** | All 16 logged final-run ingestion calls and the live cache probe used the exact mask. Detail view separately requests only rating and regularOpeningHours, cached lazily. |
| F.6.5 Actual prompt caching and measured on/off costs | **PASS** | Final real-source run: 97.22% request cache-hit rate. Twenty controlled on/off calls plus two warm follow-ups; full measurements below. These are actual calls, not only counterfactual estimates. |
| F.7.1 Flag false in built Release and TestFlight configurations | **FAIL** | Built Release runtime verified off. No TestFlight archive/distribution runtime was tested. |
| F.7.2 No reachable entry point when flag is off | **PASS** | Release simulator three-tab navigation and directory-absent Debug three-tab navigation observed. |
| F.7.3 Placeholder contains only a view and the one allowed storage key | **PASS** | Only index.tsx; one boolean AsyncStorage key, console interest event, no network/model capability. Existing persisted confirmation is supported by the pre-addendum test, not reasserted as a new tap measurement. |
| F.7.4 No direct outside imports or core-layer imports | **PASS** | No direct import from the placeholder directory; optional Debug discovery loads the view, which only imports React, React Native and AsyncStorage. |
| F.7.5 Delete directory, complete all required checks, then restore | **FAIL** | Both native configurations and 28 backend/12 app tests passed while absent, Debug remained usable; directory restored and 29/12 checks passed. Aggregate golden accuracy and physical-host checks did not pass, so the strict combined criterion fails. |
| F.8.1 Every golden save terminal within 60 seconds | **PASS** | 40/40 attempts terminal; maximum 41.396s. Failed/no-content states count as terminal, not successful extraction. |
| F.8.2 Kill mid-processing and relaunch loses nothing and leaves nothing stuck | **FAIL** | Lease expiry, one retry, checkpoint reuse, preserved edits and local queue acknowledgement recovery pass automated tests. Full native mid-processing kill/relaunch loop was not executed for this build. |
| F.8.3 First launch offline/no account/no location, queue then auto-resolve | **FAIL** | Offline first-launch SQLite/queue tests pass. Complete native share-host → reconnect → resolve/file loop not run for build 20. |

The full report, original audit, all 40 URL results, complete YAML and measured cost tables are at `/Users/krishwaghani/Desktop/ReelBot-refactor-report.md`. The fixture labels are provisional; none is presented as fully hand-verified video truth.
