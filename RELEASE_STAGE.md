# Release 32 — schema expansion stage

Based on the verified live revision `1ed818c3`.

- Existing extraction, library and authentication contracts remain unchanged.
- Apple sign-in is disabled for this release by the user's choice.
- Deploy hooks only check schema. They never apply migrations.
- `db.staged_migrate accounts-expand --apply` is the only approved migration
  at this stage. It adds unused columns/tables, without triggers or row rewrites.
- A sanitized personal recovery snapshot must be restored into an isolated
  database and its ownership/note/folder fingerprint verified before applying it.
- Accounts activation and retention are separate later stages, gated by an
  actual phone check. No retention purge or contraction is part of this stage.
