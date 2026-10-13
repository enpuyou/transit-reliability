# transit-reliability

Seattle transit reliability analytics for King County Metro (KCM) planners.

- Scope and phases: `design_doc.md`. Current state and next steps: the newest file in
  `docs/status/` (read it first).
- Every check/run/verify procedure: `docs/commands.md`. Add new procedures there.
- How data flows from KCM to Databricks, and why: `docs/collection_pipeline.md`.
- Decisions: `docs/decisions/`. Free-tier limits and usage: `docs/limits.md`.

## Architecture

A Cloudflare Worker (`workers/collector`, cron every minute) PUTs raw KCM GTFS-Realtime
bytes to `/Volumes/workspace/transit_landing/raw/<source>/dt=<UTC date>/<epoch_ms>.pb`.
A Databricks job (Auto Loader, `availableNow`) parses them with the pure functions in
`gtfs_rt/parse.py` into bronze Delta tables. Push, not pull: Databricks Free Edition
blocks most outbound internet.

## Commands

```bash
uv run --group dev python -m pytest -q
uv run --group dev python -m ruff check .
uv run --group dev python -m mypy src tests
cd workers/collector && npm run typecheck && npm test
```

Run all of these before committing; CI runs the same.

## Never

- Never interpolate a dotted volume name (`workspace.transit_landing.raw`) into a file
  path. Use `bronze.schema.volume_path()`.
- Never use `entity_id` as a key; it changes every pull. Dedupe vehicles on
  `vehicle_id` + `vehicle_ts`, trip updates on `trip_id` + `start_date` +
  `stop_sequence` + `feed_header_ts`.
- Never filter or dedupe in the collector or bronze. The feeds are not recoverable;
  reduce volume in silver (decision 0002).
- Never deploy the `prod` bundle target casually: it is unpaused and spends compute
  immediately. Dev ingest is deliberately paused.
- Never commit `.env`, print token values, or put secrets in docs or `wrangler.toml`.
- Never query the Databricks warehouse from a public/serving path.

## Gotchas

- Databricks CLI: always `--profile enpu` (the `DEFAULT` profile is stale). `databricks api`
  ignores the bundle profile.
- Serverless is Spark Connect: no `rdd` or `createDataFrame(rdd)`. Use `mapInPandas`
  inside `foreachBatch`, nullable `Int64` for optional ints, job
  `environment_version: "3"`, and only `availableNow`/`once` triggers.
- To rebuild a bronze table, drop the table and delete its checkpoint
  (`workspace.transit_ops.state/checkpoints/bronze_<source>`) together.
- `*-smoke.pb` files in the landing volume are manual tests, not collector output
  (collector files have numeric epoch names).
- Each pull is a snapshot of one instant. Collection began 2026-10-13 about 21:43 UTC;
  there is no backfill.
- Cloudflare free plan allows 10 ms CPU per cron run; we use 6 to 10 ms (at risk, decision
  0003). Check it (see `docs/commands.md`) after any change to the Worker.
- Never move sources to a new Worker without first confirming the new cron fires. A new
  Worker's cron did not fire on 2026-10-13 and caused a gap. Databricks compute and storage
  quotas are unpublished, so measure rather than assume.
- macOS has no `timeout`; use `perl -e 'alarm N; exec @ARGV' cmd`.

## Conventions

- Python 3.12, `uv`, ruff (line length 100), mypy strict. Import Spark/pandas inside
  functions so the logic stays testable locally.
- Bronze column contracts: `bronze/schema.py`. Tests use `tests/fixtures/*.pb`.
- Dev tables use schema prefix `dev_transit`; prod uses `transit`.
- Conventional commits; push only when asked.
- Data reports follow `docs/data_reports/_template.md`. State measured numbers with a
  date and mark unmeasured claims as unverified.
- When behavior changes, update `docs/commands.md` and the relevant doc in the same change.
