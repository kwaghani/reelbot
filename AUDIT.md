# Personal-product audit

The refactor replaces transport-driven ingestion and shared libraries with a personal place saver. The app now owns a durable local SQLite library, optional Apple sync, a native URL queue, personal folders, notes, and search. The API derives ownership from a device credential and never accepts an owner from a request body.

Semantic embeddings and cosine ranking were retained in `worker/search.py`; interactive conversational code was removed. The source media helpers now live in `worker/media.py`, and one strict array extraction call lives in `worker/pipeline.py`. Address resolution uses the global cache in `worker/places.py`.

The original nested app history and both environment files are preserved in the external full backup. The app is now tracked by the outer repository. The original audit table, with corrected classifications and original line references, is delivered separately outside this clean source tree.

The forward migration was exercised with single-owner, multiple-owner, and no-owner fixtures and with a restored copy of the live public schema. In the actual live database, all 21 originals lacked a resolvable device identity. All 21 were preserved intact as orphan records; none received an arbitrary owner.

Executed checks and unresolved acceptance failures are documented in `VERIFICATION.md` and the regenerated `audit-evidence/`. No source audit can substitute for a physical-device share-sheet test or a fully labeled real-video accuracy evaluation.
