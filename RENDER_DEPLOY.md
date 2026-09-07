# Render deployment

`render.yaml` defines a Docker web service and Docker worker. The API needs `DATABASE_URL`, `GOOGLE_MAPS_API_KEY`, and the Apple bundle audience. The worker additionally needs `ANTHROPIC_API_KEY` and its extraction/model settings. Provider secrets are supplied in Render, never committed.

Before deploying this schema change, take and restore-test a database backup. Run the migration dry run, inspect all ownership buckets, and apply the atomic migration. Deploy the API and worker from the same revision. The Docker build installs ffmpeg/tesseract and preloads the embedding model; provision enough memory for semantic search and media processing.

The API starts with `deploy/render/start-api.sh`. The worker starts with `deploy/render/start-worker.sh`. Health checks use `/healthz`. Configure the iOS build with the resulting HTTPS API URL. Test device registration, one real save, sync, and ownership isolation before distributing a build.

At the time of this refactor, the existing Render services were suspended by the owner. The refactor does not claim a live deployment. Reactivating a paid service and choosing its deployed revision remain operational steps. The supplied configuration contains only the personal API and worker.
