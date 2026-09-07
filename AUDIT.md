# ReelBot reliability audit

Audit date: 2026-09-06. Existing work in the root repository and the separate,
ignored `app/` Git repository was preserved. No applicable AGENTS.md was found.
This is evidence from local testing, not a production release sign-off.

## Actual scope

ReelBot is an Expo iOS app/share extension and a WhatsApp group bot. It stores
source links and extracted knowledge; it does not host videos. Users choose a
display name and a library: Shared Saves (shared with all testers), or a private
invite-code group. The four tabs are Saved, Folders, Groups and Ask. Folders are
automatic, with topic and location views. Members can copy records between groups
they belong to, delete records for the whole group, and ask questions with source
links. Playback opens the original platform. WhatsApp imports group links,
answers questions, and sends scheduled nudges.

Sources supported by implementation: Instagram reel/reels/p URLs, TikTok video
and short URLs, and YouTube watch/shorts/youtu.be URLs. The API accepts pasted
links; the main native app imports through the system share sheet and copies
existing records. There is no main-app paste form.

Not implemented: password registration/login/logout/recovery, cross-device
accounts, editable profiles, public item links/login redirects, individual
recipients, uploads, hosted/embedded playback, favorites, manual tags/notes,
reactions/comments, undo/trash, group leave/kick/block/moderation, push/email,
unread badges, pagination, desktop/web UI. These were not invented for the audit.
Device sessions, group membership, copies, saver counts and deletion are the
applicable equivalents of the requested account and interaction journeys.

## Evidence definitions

- **DB/API**: real FastAPI handlers and Postgres transactions in disposable
  `reelbot-audit-db`, Supabase Postgres 17.6.1.106, loopback port 55439. Three fresh
  server-issued device sessions, including two with the same display name.
  Extraction and vectors are substituted at external boundaries in these tests;
  authorization, queue handling, persistence, copies, deletion and rollback are
  real. This does not prove live provider integration.
- **Logic/client**: Python classifier/retrieval/source tests, API smoke check,
  TypeScript tests with controlled network/storage responses, and type checking.
- **Transport**: real Postgres with a fake in-process `sendMessage` adapter. It
  cannot contact WhatsApp. No real recipient received an audit message.
- **Native**: dedicated iPhone 17 / iOS 26.5 simulator named ReelBot Audit,
  Release build, loopback API, query-only worker with paid provider keys disabled.
  Clearly labelled synthetic UI fixtures were seeded in Shared Saves. Copying,
  browsing and deleting those records are real; they are not live imports.
- **Public source probes**: no cookies, AI or video download. Unavailable
  Instagram/TikTok URLs were rejected. An unavailable YouTube URL initially
  produced a fake `youtube video #...` title; after the fix it was rejected.
  The public YouTube URL `https://www.youtube.com/watch?v=jNQXAC9IVRw` returned
  title `Me at the zoo`, creator `jawed`, canonical source and a thumbnail field.
  This is metadata verification, not AI extraction or thumbnail image accuracy.
- **Offline model**: the cached BAAI/bge-small-en-v1.5 model actually generated
  384-dimensional vectors and persisted them for two labelled local fixtures.
  Network/model downloads were disabled for this check.

## Feature checklist

Verified is limited to the stated method. Blocked means necessary external
access/device evidence is missing. Incomplete describes remaining behavior or
coverage; passing substitutes do not remove those limitations.

| Feature | Outcome | Evidence and limits |
| --- | --- | --- |
| Display-name onboarding/device registration | Verified | Native form, long name, disabled empty submission, real server-issued session; concurrent registration and storage failure tests. |
| Session/name/library persistence | Verified | Native reinstall/relaunch restored the same name, private library and saved records; client storage tests restore without another registration. |
| Device connection recovery | Verified client checks and native outage recovery | API outage displayed a connection error; restart/refresh cleared it. Permission failures clear cached private cards. Native session revocation hid private records; reset created a new default-only session; invite-code rejoin restored the existing group/record. No password-account recovery is claimed. |
| Shared default library | Verified | Native empty state and explicit shared-with-all-testers label; automatic default membership verified by API. |
| Private group create/join/list | Verified | Native create and real second-session join; open native group updated from two to three members automatically. API membership/counts, invalid codes and ten-attempt throttle. |
| Group invite | Verified in simulator, delivery blocked | Native system share panel opens with correct group invite text/code; no invite sent to a real recipient. No item deep-link behavior exists. |
| Selected library/App Group settings | Verified in signed simulator; physical distribution blocked | Shared defaults contained the correct session/private destination. Actual Safari share queued to that group. Fixed native bridge JSON-string decoding and used Xcode simulator entitlements. Physical signed distribution still needs verification. |
| Source validation | Verified | Python/JS tests cover supported patterns, tracking normalization, malformed/profile URLs, spoofed hosts and platform-scoped identities. |
| URL submission API | Verified | Real 202/queued/job ID; authenticated device and selected group required; malformed responses cannot claim success. |
| iOS share extension | Verified simulator submission/failure; physical platforms blocked | Text without URL yields clear error/Close. Actual Safari share returned 202, persisted authenticated group job/receipt, appeared as Processing, and became a useful unavailable-source error after real worker extraction. Zero saved records created. Full valid Instagram/TikTok imports remain blocked. |
| Instagram/TikTok extraction | Blocked for complete live coverage | Actual unavailable-source probes rejected; controlled metadata HTML/worker failure tests passed. Valid creator/private/authenticated-source coverage needs platform test accounts. |
| YouTube extraction | Verified live metadata; full extraction blocked | Public title/creator/source fields correct, unavailable placeholder fixed and re-probed. yt-dlp warned that a supported JS runtime was missing; download/format coverage is not claimed. |
| Captions/facts/creator/thumbnail metadata | Verified controlled parsing; live fidelity blocked | Apostrophes/reordered HTML attributes and structured-content priority tested. Creator/thumbnail are not separate native card fields. Vision/transcription/AI facts not live verified. |
| Import queue/idempotency | Verified | Concurrent duplicates coalesce, stable request IDs reject conflicting payloads, stale claims recover, empty/unreadable output errors, partial writes rollback. |
| Saver identity/counts | Verified | Two same-name users remain distinct savers; count reaches two, persists, and repeated save by one user does not inflate it. |
| Copy between groups | Verified | Native folder filter/selection and real two-record copy; recipient API sees both, unrelated session gets 403. API tests cover repeated/concurrent/null-place copies and all-or-nothing missing selection. |
| Saved feed/order/refresh/states | Verified DB/API and native saved display | Stable newest-first SQL, latest job chosen before status filtering, job/time fields, foreground/five-second refresh, scoped receipts. Native Processing changed to error automatically after real worker failure, without duplicate cards. |
| Automatic folders/classification | Verified deterministic logic; AI blocked | Existing 24 classifier/retrieval tests pass. Native Recipes → Pasta hierarchy and counts checked. Live AI folder fidelity needs provider sandbox. |
| Location folders/place verification | Verified native hierarchy and controlled logic; Places blocked | California → San Diego native hierarchy; unverified places keep content without invented coordinates. Paid Places matching not called. |
| Delete for entire group | Verified DB/API and native | Both members lose deleted record, saver rows removed, related import jobs cancelled, independent copy remains, unrelated deletion denied. No undo promised. |
| Original-source opening | Verified in simulator | Native card opened the correct YouTube page/title. External video imagery was observed; hosted playback, audio, seek and physical-device behavior are not claimed. Failed native opening now produces an error. |
| Ask/query/follow-ups/citations | Verified queue/logic; live AI blocked | Native Hello produced the real worker's greeting; persisted job is done with matching reply. API timeout returns processing/job ID; client polls real answer and retries terminal failures correctly. Grounding failure returns source-only fallback. |
| Local chat/New chat | Verified logic and native persistence/New chat | Native reinstall restored Hello and the actual worker reply; New chat cleared it. Group-specific storage, hydration/send guard, visible storage errors and stale reply suppression tested. Process-killed unfinished replies are not automatically restored. |
| WhatsApp receive/import/questions | Verified parser/DB contracts; live blocked | Quoted reel is not re-imported; bot/history messages excluded; multiple URL dedupe and stable message request IDs. No WhatsApp account connected for this audit. |
| WhatsApp replies/recipient selection | Verified real DB/fake sender; live blocked | Only group chat jobs, configured target filter, one overlapping poll wins, stable transport IDs, sent state persisted. App jobs never routed to WhatsApp. |
| Scheduled nudges/cooldowns | Verified real DB/fake sender; live blocked | App groups excluded; concurrent enqueue once; failed send remains pending, other messages proceed, retry records one successful delivery event. |
| Nudge impact report | Verified local SQL/transport tests | Counts confirmed linked sent_at only, engagement window starts at send time, queued-only nudges correctly count as zero. Legacy unlinked rows excluded. |
| Organizer/reindex maintenance | Verified local execution | Organizer dry-run proposed one change on two fixtures; apply checked separately. Real offline reindex persisted two 384-dimensional vectors. No production records changed. |
| API and database privacy | Verified real negative requests/roles and native recovery | Missing/forged bearer, spoofed device, unrelated group/item/job/query/copy/delete denied. Revoked sandbox membership denies subsequent reads/polling. anon/authenticated roles denied on all ten tables. |
| Failure recovery/atomicity | Verified automated | Bounded requests/providers/subprocesses, abort handling, transaction rollback, stale response guards, worker reconnection, clear error rather than false success. Native API stop showed a clear connection error; restart and refresh recovered. |
| Native usability/accessibility | Verified limited simulator observations; full accessibility incomplete | Onboarding, long names/titles, tabs, forms, picker, cards and folders inspected. Fixed picker stretching, tab labels, touch targets and separate delete control. Calculated contrast improved. Full VoiceOver/hardware keyboard/physical device testing remains. |
| Deployment/configuration | Blocked remote rollout | Local Release build succeeds. Additive schema applied twice. Coordinated schema/server/client rollout documented; no remote service or App Store deployment performed. |

## Acceptance journeys and persisted evidence

1. **New sender → import → recipient**: real API queue and worker-write journey
   with controlled extraction; resulting item/source/folders read by joined
   recipient and denied to unrelated user. Native onboarding/group creation and
   actual copy of labelled fixtures are separately verified. Complete live
   Instagram/TikTok/AI import remains blocked.
2. **Logged-out recipient opens shared link**: no item-link/login-redirect
   feature exists. Invites carry a code entered after device onboarding. Missing
   session receives 401; possessing a group/item UUID is insufficient.
3. **Save/organize → refresh/new session**: DB folder/item/saver persistence and
   restored client session tested; native reinstall restored private scope and
   both copied records. Local BGE reindex produced real persisted vectors.
4. **One user interacts → another sees state**: distinct same-name saver count
   reaches two. Native two-record copy was confirmed by recipient HTTP 200 with
   correct titles/folders/counts; unrelated HTTP 403. Copies are independent.
5. **Delete/revoke → recipient loses access**: automated real DB deletion removes
   record and saver rows for both members while preserving another-group copy.
   Direct sandbox membership removal immediately denies subsequent API/job access.
   No membership-removal UI is claimed.
6. **Invalid/unavailable source**: malformed URL rejected before queue; empty or
   failed extraction becomes a recoverable error without blank item. Real public
   unavailable probes and YouTube retest are recorded above.
7. **Unrelated user**: three-session direct API and direct database-role checks
   establish server-side privacy; hiding controls is not treated as authorization.

## Important fixes

- Server-issued bearer sessions replace caller-chosen IDs as authorization.
  Only token hashes are stored in the database; names/UUIDs cannot impersonate a
  private-group member. RLS/client table grants were tightened.
- Atomic ingest writes, stable source identity, per-request idempotency, distinct
  saver identity, all-or-nothing copies, cancellation during deletion and queue
  recovery. Testing exposed and fixed a real missing UUID cast in copy SQL.
- Removed misleading Saved/empty-import/fake-query-answer success paths. Added
  job IDs/status polling, bounded waits, meaningful errors and retry behavior.
- Decoded both string and object results from the native shared-preferences bridge; its installed JS wrapper returned JSON text despite its generic type. Added a regression for settings and receipts.
- Permission errors clear cached private views; invalid sessions offer reset/rejoin and no longer masquerade as shared-storage or empty-library errors.
- Isolated client group/history state, serialized selected destination storage,
  fixed stale count/data refresh, removed unsafe delete snapshot rollback, and
  corrected accessible labels/targets/contrast and native picker sizing.
- Isolated WhatsApp recipients, serialized delivery, stable transport IDs,
  accurate sent-state events and nudge impact attribution.
- Hardened public metadata parsing, redirects, login/unavailable-page rejection,
  weak place matches and ungrounded-answer fallback; bounded external work.

## Automated results

Full `scripts/run_checks.sh` passed: **41 Python tests**, API smoke check,
**5 transport tests**, **10 client tests**, TypeScript check, Python compilation
and listener syntax check. No integration tests skipped. Both Git diffs pass
whitespace checks. After the full script passed, final recovery messaging and empty-state follow-ups passed the client suite, TypeScript check and Xcode simulator Release build.

The schema was applied twice. Direct SET LOCAL ROLE checks denied both client
roles on app_devices, app_join_attempts, groups, members, items, item_saves, jobs,
events, outbound_messages and nudges. Tests use guarded loopback audit URLs.

The initial native Release build and subsequent fixes built successfully with
Xcode 26.5. The unsigned build could not share App Group defaults; rebuilding
with Xcode simulator signing created the proper shared container. Simulator
results do not prove physical share-sheet entitlements or App Store distribution.

Secondary text initially measured 4.40:1 on muted cards. It was darkened to meet
4.5:1 on checked muted surfaces. Primary, accent, warning, danger and chat text
pairings were also calculated. This is not a full accessibility certification.

## Remaining limitations and rollout

- Coordinate additive `db/schema.sql`, API, worker, listener and rebuilt iOS app.
  Old API-key-only clients are deliberately rejected. Existing records remain,
  but old public device IDs cannot safely claim new sessions; users must rejoin
  private groups with invite codes. See VERIFICATION.md. Device tokens use native
  local/App Group storage; there is no cross-device account recovery service.
- Use explicit sandbox/test provider access for AI, Places, vision, transcription
  and WhatsApp delivery, plus dedicated platform test accounts for valid/private
  Instagram/TikTok sources. Existing credentials were checked by presence only;
  no paid call, production write or real notification was performed.
- Configure a supported yt-dlp JS runtime before claiming full YouTube downloads.
  Published model documentation does not establish model access for the configured
  account; keep this as a provider release check.
- Verify physical iPhone sharing, platform login/playback, VoiceOver, keyboard,
  larger text and signed distribution. No desktop product or Android target is
  configured, and neither is represented as tested.
- Existing place records merge by group/place ID and use the latest source/data.
  This inherited saved-place behavior is not a per-reel archive. Full process
  interruption does not automatically restore an unfinished Ask reply; the
  question can be resubmitted. Chat is device-local and group-specific. Membership counts identify enrolled device sessions, including older/reset devices, rather than deduplicated human accounts.
- Large-library/load behavior is unverified; no pagination exists. Remote
  deployment/service health and third-party delivery guarantees remain blocked.

## Native final verification ledger

- Final client suite: ten tests; full check script exited 0. Compact output is in `audit-evidence/checks.txt`.
- Native source queue: job eb49930b-79bd-4fab-a48d-a2d28e8f3703 belonged to the native session and Audit Friends. Real worker result: error, item_id null, zero items for the unavailable source. Main app automatically replaced Processing with Could not save reel.
- Native deletion: recipient private library changed from two records to one; original Shared Saves retained two. Source and delete are separate accessibility elements after the fix.
- Reinstall retained name, identity, private library and actual completed chat. New chat confirmation cleared that conversation.
- API outage produced Cannot connect right now; restarting and refreshing removed the error while retaining the correct saved record.
- Native revoked session hid private cards and showed the correct session error (without false storage/empty-library claims). Reset created a new device identity that could see only Shared Saves. Entering the existing invite code restored Audit Friends and its remaining record. Server responses and persisted membership agree with the native view.
- Organizer dry-run and apply succeeded on the two labelled fixtures; one folder changed and real 384-dimensional vectors remained persisted. A second dry-run proposed zero changes.
- Open group automatically updated from two to three members after another test session joined, without manual refresh. Final picker shows compact 44-point filter rows and correct folder data after organizer apply.
