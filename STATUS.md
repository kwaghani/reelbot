# ReelBot status — September 7, 2026

The app supports ten registry-defined content types, generic entry editing, automatic type/facet folders, personal search, MapKit, and optional Apple sync. Release has Recent, Map and Settings; Debug adds an isolated interest view. The native shared-container queue remains durable and queue-only.

29 backend checks, 12 app checks, type checking and Python compilation pass. Debug and Release simulator builds and signed iPhone build 20 pass. The iPhone 15 Pro is unavailable, so build 20 has not been installed; build 19 was the last confirmed installation. A signed build archive is at `/Users/krishwaghani/Desktop/ReelBot-build-20.zip`.

The final 40-source attempt produced 46 entries, all saves terminal within 41.396 seconds, and a 10.87% review rate. The five-place listicle and four empty cases pass. Release acceptance remains FAIL: labels need independent completion, source access failures and mixed-content misses remain, and physical-host/first-launch acceptance checks are unexecuted. Detailed checks are in VERIFICATION.md.

The live migration preserved all 21 ownerless originals in quarantine; no owner was guessed. The separate phone-test database preserved all 8 original personal entries. Controlled UI fixtures have been removed.

Test builds use the Mac-hosted API at `http://100.70.22.235:8123`; keep the Mac and iPhone on a mutually reachable network. Local API/worker processes are running. Render API/worker services remain suspended; this is not an always-on deployment. Reconnect and unlock the iPhone to install build 20, then run the physical share-host checks in SHARE_TEST.md.
