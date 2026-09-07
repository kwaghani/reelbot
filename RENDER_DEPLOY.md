# Render deployment

`render.yaml` defines a Docker web service and worker. The Docker context includes the runtime `config/content_types.yaml`. The API needs the database URL, place provider credential and Apple bundle audience. The worker also needs its extraction provider credential and model/cost settings. Supply secrets through Render, never through app public configuration.

Back up and restore-test the database before migration. Run `python db/migrate.py --dry-run`, then apply all forward migrations. Deploy API and worker from the same revision. The image installs ffmpeg/tesseract and preloads the embedding model; allocate enough memory for semantic indexing and media work.

Entry points are `deploy/render/start-api.sh` and `deploy/render/start-worker.sh`; health checks use `/healthz`. Configure shipping iOS builds with the resulting HTTPS API URL. Verify registration, a real save, sync, registry updates and owner isolation before distributing the app.

The existing Render services remain suspended. This branch has been built and tested locally; it is not a live service rollout. Resume/deploy the API and worker together when running the production service, then replace the temporary local test URL in a new phone build.
