# ReelBot operations

Run the API and worker against the same migrated PostgreSQL database. `/healthz` checks database reachability. `/sync`, `/items`, `/share`, `/jobs/{id}`, folder changes, and cost summaries require a device bearer credential. A public device identifier alone cannot claim an existing identity. Apple identity tokens require signature, issuer, audience, expiry, and one-use nonce verification.

The API uses trusted server database credentials. Direct client table privileges are revoked and RLS is enabled. Every personal query filters by authenticated owner; composite foreign keys prevent folder links from crossing owners. Never expose the database connection string or provider credentials to an Expo public setting.

Processing attempts run in a separate process. The supervisor enforces a deadline, and a child also expires if the supervisor is killed. An expired processing lease becomes `failed` with a readable reason. It retries automatically once after 15 seconds. Retry in the app resets the time/attempt budget while retaining cumulative measured provider usage. Original URLs, available signals, extracted candidates, and completed lookups remain durable across interruption.

If a source is private, expired, blocked, or unavailable, open the source and retry. A missing audio track or unreadable frame does not discard a readable caption. A low-confidence address remains in Needs Review. The owner can supply a better venue name and city. An empty extraction is displayed as No places found.

The migration preserves unowned original records in `orphaned_items`; it never guesses a person. Recovery requires evidence of ownership and an explicit administrative procedure. Keep the pre-migration backup outside the repository with restrictive permissions. Do not delete preserved originals to make row counts look clean.

Run `python -m worker.organize_library --user-id UUID` to repair automatic filing. Run `python -m worker.reindex_embeddings --user-id UUID` to inspect a reindex count, adding `--apply` to write vectors. The normal worker also maintains missing vectors in a separate process. Semantic failure falls back to lexical search and logs the failure.

Provider usage is stored per save. Transcription and OCR use local compute and have zero API fees. Token and lookup charges are estimated from configured rates. Detail-view requests are separate from ingestion and cache their extra fields for 30 days.
