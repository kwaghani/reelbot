# Executed verification

This record distinguishes executed checks from acceptance failures. An unavailable check is FAIL. Provider-boundary tests do not establish real-video accuracy or physical share-sheet performance. Final measurements and the URL-by-URL table are in the accompanying execution report and `audit-evidence/`.

The test environment uses disposable loopback Postgres databases whose names contain `test`, separate from the migrated live database. Run `TEST_DATABASE_URL=<disposable loopback URL> scripts/run_checks.sh`. This runs database integration tests, real SQLite application tests, TypeScript checking, and Python compilation. Never use the live database for this command.

| Requirement | Result | Evidence / limitation |
|---|---|---|
| 5.1 Both exact source scans return zero hits | PASS | Both exact commands returned zero hits; `audit-evidence/purge-scans.txt`. |
| 5.1 Retired listener directory absent | PASS | Entire directory removed, including dependency tree. |
| 5.1 Removed transport environment settings absent | PASS | Eight first-party configuration files checked; no retired settings. `audit-evidence/source-checks.json`. |
| 5.1 Backend/app build and full tests | PASS | Python compilation, 14 backend/integration tests, 7 SQLite/client tests, TypeScript; Debug and signed Release simulator builds. Both configurations also built with the placeholder directory removed; `audit-evidence/builds.json`. |
| 5.2 Dry run includes three ownership buckets | PASS | `audit-evidence/migration.json`: 0 single, 0 multiple, 21 unresolved, total 21. |
| 5.2 Retired ownership/transport tables dropped | PASS | Live migration catalog verification; target public schema only. |
| 5.2 All originals accounted for | PASS | Live: 0 personal rows + 21 orphans = 21 originals. Synthetic fan-out test accounts by distinct original IDs because multi-owner records produce multiple rows. |
| 5.2 No arbitrary owner | PASS | All 21 unresolvable live records preserved with full payload; synthetic identity mapping exercised. |
| 5.3 Precision at least 85% | FAIL | No independently verified complete signal-specific labels; numerator/denominator unavailable, reported as null. |
| 5.3 Recall at least 75% | FAIL | Required 6/4/3/3/2/2 fixture composition and full-video labeling incomplete. |
| 5.3 Zero silently missed venues | FAIL | Every extracted candidate is persisted before lookups; missing-source venues cannot be enumerated without complete labels. |
| 5.3 Both no-place videos correctly empty | FAIL | Actual outputs available, but full-video labels remain unverified. |
| 5.3 Five-plus listicle creates separate entries | PASS | Five separate entries from `DUpDIg1k87g`; 12 from `DJEzB4SM_o4`. Full recall of the larger list is unverified. |
| 5.4 All three native host share sheets | FAIL | Required native host apps unavailable on the audit simulator; paired physical devices unavailable. Safari is supplementary evidence only. |
| 5.4 All three return under 400 ms | FAIL | Safari measured 236.315 ms to completion dispatch, queue write 15.711 ms. No measurements from the required three native hosts. |
| 5.4 Backgrounded and force-quit main app | PASS | Safari native extension queued with the app backgrounded and after terminating it. Completion dispatch was 236.315 ms and 193.571 ms respectively. Required native-host matrix remains untested. |
| 5.4 Airplane mode reconnect without action | FAIL | Local offline queue is tested; physical OS background/reconnect test unavailable. iOS does not promise execution after force-quit. |
| 5.4 Fifty consecutive shares without crash | FAIL | 50 native filesystem writes and 50 application-layer saves pass; these are not 50 native-host extension presentations. |
| 5.4 Duplicate reel gives one entry | PASS | Real SQLite tests include concurrent duplicate variants and restart-safe acknowledgement; API unique per-owner source hash verified. |
| 5.4 Zero extension-origin network calls | FAIL | Native writer contains no network code and no React runtime; runtime traffic attribution not captured. |
| 5.5 Save visible within two seconds of cold launch | FAIL | Local saved row visibly renders; no defensible cold-launch UI timing capture was completed. |
| 5.5 Every real-URL save terminal within sixty seconds | PASS | All 20 direct worker attempts terminal; maximum 45.645 seconds. Four sources failed access. Offline/OS scheduling wait excluded. |
| 5.5 Resolved coordinates/address valid | PASS | All 15 resolved candidate rows have valid addresses/coordinates; `audit-evidence/golden-integrity.json`. |
| 5.5 Every resolved place has city/category folders | PASS | All 15 resolved candidate rows have city and category assignments; real-run SQL and integration assertions. |
| 5.5 Custom deletion preserves places | PASS | Database and local operation tests retain the places and automatic folders. |
| 5.5 Search matches name, city, note | PASS | Real database lexical/vector ranking, actual embedding smoke run, and offline SQLite search tests. |
| 5.5 Kill during processing and relaunch without loss | PASS | Simulator app terminated while the Instagram source was processing; relaunch retained both sources and all five resulting venues. No processing rows remained. Backend interrupted-lease recovery and bounded retry also pass. |
| 5.5 First offline launch without account | PASS | SQLite tests with network/identity unavailable; signed simulator opens without onboarding and preserves a queued link during service failure. |
| 5.6 Debug screen measured cost | PASS | Built signed Debug app visibly showed two completed saves, $0.0821/save and five address lookups, while placeholder directory was absent; `audit-evidence/debug-cost.png`. Rates estimate provider charges; local compute excluded. |
| 5.6 Five sources use one global lookup | PASS | `audit-evidence/live-cache.json`: actual Google request, five distinct source URLs, same controlled extracted candidate, two owners, one call/four hits. This bypasses extraction. |
| 5.6 Ingestion masks contain only required fields | PASS | Live request logging and cache test use the five specified fields with the required API `places.` prefixes. Lazy detail requests are separate and intentionally use detail fields. |
| 5.7 Built Release/TestFlight flag disabled | FAIL | Signed Release navigation and native compilation verified; no TestFlight archive/delivery built or tested. |
| 5.7 Flag-off entry point unreachable | PASS | Installed Release Settings has no experimental entry; Release application test rejects even an incorrectly true native flag. |
| 5.7 View-only directory and one allowed key | PASS | Directory inspection: one view, one boolean-value AsyncStorage key, no networking/model files. |
| 5.7 No imports from outside or into core | PASS | Generic optional discovery; no explicit imports into the directory; view imports UI and the permitted storage API only. |
| 5.7 Delete directory and all 5.3–5.6 pass | FAIL | Removed directory while rerunning tests, evaluation and build; no dependency failure. Underlying physical/label acceptance failures persist. Directory restored afterward; full tests pass again. Both native configurations built without it, and actual Debug cost/Safari queue/real-URL checks executed while absent. |

Backups remain outside the source tree. The live database dump was restored into a disposable database before migration. No existing deployment was resumed: the API, worker, and retired scheduled service were already suspended in Render. This is an implementation and test branch, not a completed production rollout.

Final evaluation cost: $0.03480095 per source on average; 21 measured Text Search calls, four cache hits, 16% hit rate. Cache was warm from earlier runs. This is measured usage multiplied by configured rates, not invoice reconciliation; interrupted unreported provider work and hosting compute are excluded.
