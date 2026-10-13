# transit-reliability

Seattle transit reliability analytics for King County Metro (KCM) planners. Source of truth
for scope and phases: `design_doc.md`. Current state: latest file in `docs/status/`.
All commands (check, run, verify, troubleshoot) live in `docs/commands.md`; keep it updated
whenever you add a procedure.

## Architecture in one paragraph

A Cloudflare Worker (`workers/collector`, cron every minute) fetches the KCM
GTFS-Realtime feeds and PUTs the raw bytes to the Databricks volume
`/Volumes/workspace/transit_landing/raw/<source>/dt=<UTC date>/<epoch_ms>.pb`. A Databricks
job (`ingest_job`, every 30 min in prod) uses Auto Loader (`binaryFile`, `availableNow`) and
the pure parsers in `src/transit_reliability/gtfs_rt/parse.py` to append to bronze Delta
tables. Push, not pull: Databricks Free Edition restricts outbound internet. Details and
reasons: `docs/collection_pipeline.md`, `docs/decisions/`.

## Commands

```bash
uv run --group dev python -m pytest -q
uv run --group dev python -m ruff check .
uv run --group dev python -m mypy src tests
cd workers/collector && npm run typecheck && npm test
databricks bundle validate -t dev && databricks bundle deploy -t dev
databricks bundle run ingest_job -t dev
```

CI (`.github/workflows/pr.yml`) runs the same Python and collector checks. Run all of them
before committing.

## Gotchas that have already cost time

- **Databricks profile:** always use `--profile enpu` (the `DEFAULT` profile is stale).
  `databricks api` ignores the bundle profile, so pass `--profile enpu` explicitly.
- **Volume names vs paths:** config uses dotted names (`workspace.transit_landing.raw`),
  file paths need slashes. Always convert with `bronze.schema.volume_path()`; never
  interpolate a dotted name into `/Volumes/...`.
- **Serverless compute (Spark Connect):** no `rdd`, no `createDataFrame(rdd)`. Use
  `mapInPandas` inside `foreachBatch`. Use nullable `Int64` in pandas for optional ints,
  otherwise NaN floats break `LongType`. Job environments need `environment_version: "3"`.
- **Only `availableNow`/`once` triggers** work on serverless streaming.
- **`entity_id` is not a key.** It looks like `<snapshot epoch>_<n>` and changes every pull.
  Vehicles: dedupe on `vehicle_id` + `vehicle_ts` (about 5 duplicate vehicle IDs per
  snapshot). Trip updates: `trip_id` + `start_date` + `stop_sequence` + `feed_header_ts`.
- **Checkpoint and table go together.** To rebuild a bronze table, drop the table and
  delete its checkpoint under `workspace.transit_ops.state/checkpoints/bronze_<source>`.
- **Smoke files** (`*-smoke.pb`) sit in the landing volume from early testing. They are
  not collector output (collector files are numeric epoch names) and `scripts/` skips them.
- **macOS:** no `timeout` command; use `perl -e 'alarm N; exec @ARGV' cmd`.
- **Environment:** `.env` holds live credentials. Never commit it, print token values, or
  put secrets in docs. Worker secrets are set with `wrangler secret put`, not
  `wrangler.toml`.

## Data decisions (see `docs/decisions/0002-storage-and-dedupe-strategy.md`)

- Raw files and bronze keep every pull; never filter in the collector or bronze. The feeds
  are not recoverable and unchanged predictions are the agency baseline for Phase 7.
- Reduce in silver: trip updates keep only changes (with first/last seen), alerts keep
  versions. About 67% of trip-update rows repeat between pulls.
- Each pull is a snapshot of one instant, not a time window. Collection began
  2026-10-13 about 21:43 UTC; there is no backfill.
- Trip updates dominate volume: about 35,000 rows per snapshot, about 50 million rows and
  1.8 GB raw per day.

## Free-tier limits (see `docs/limits.md`)

- Cloudflare Workers free: 10 ms CPU per cron run (measured 4 to 5 ms, the tightest
  limit), 5 crons, 100k requests/day, 200k log events/day.
- Databricks Free Edition: compute quota and storage are unpublished; overrun stops compute
  for the day. 5 concurrent job tasks, one 2X-Small warehouse. Do not query the warehouse
  from any public path. Keep ingest at 30 minutes unless measured. Re-check usage with
  `system.billing.usage` after each phase.
- Never deploy `prod` casually: it is unpaused and spends compute immediately. The dev
  schedule is deliberately `PAUSED`.

## Conventions

- Python 3.12, `uv`, ruff (line length 100), mypy strict. Spark and pandas imports happen
  inside functions so local tests run without them. Pure logic stays testable locally
  (parsers, `parse_record`, `volume_path`).
- Bronze column contracts live in `bronze/schema.py`; keep tests in `tests/` against the
  fixtures in `tests/fixtures/` (expected counts: 522 / 33,772 / 81 rows).
- Conventional commits. Commits end with the Co-Authored-By line from the session
  attribution. Push only when asked.
- Docs: data reports follow `docs/data_reports/_template.md` and the index in its README.
  Record measured numbers with a date. Mark unmeasured claims as unverified.
- Dev tables use schema prefix `dev_transit` (`workspace.dev_transit_bronze.*`); prod uses
  `transit`.

## What to do next

Check the latest `docs/status/` file first. As of 2026-10-13: collector live, bronze
verified in dev. Pending: 24-hour gap check, prod ingest and compute baseline,
`ops.job_runs` table, prod bundle `root_path`/permissions, freshness alert, then Phase 3
(static GTFS and silver).
