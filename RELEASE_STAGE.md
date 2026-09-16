# Release 32 — accounts and sync activation stage

Based on the expansion release, with accounts code selected from `5b77461e` and
the subsequently tested mutation acknowledgment/conflict protocol.

- Existing anonymous tokens and full-library/direct-mutation endpoints remain
  supported beside the new owner-scoped change feed.
- Apple sign-in is disabled for this release by the user's choice.
- Deploy hooks only check schema. They never apply migrations.
- `db.staged_migrate accounts-activate --apply` is the only activation migration
  for this stage; it requires the preceding, verified expansion release.
- A sanitized personal recovery snapshot must be restored into an isolated
  database and its ownership/note/folder fingerprint verified before applying it.
- Retention remains a separate later stage. No retention purge, coordinate-lease
  runtime or client presentation change is part of this stage. Do not deploy
  until the actual phone has passed the expansion-stage gate.

Local qualification: 17 account/session/ownership tests, three legacy-client
contracts and two conflict/disabled-Apple checks pass against a newly created
disposable Postgres database. This is preparation, not a production deployment.
