# Command Reference

A living reference for manually setting up, running, checking, and troubleshooting
`transit-reliability`.

## Safety and placeholders

- Run commands from the repository root.
- Never paste token values into this document, shell history, logs, or commits.
- `.env` is ignored by Git. Keep credentials there or in your shell environment.
- Replace values in angle brackets, such as `<workspace-host>`.
- The configured Databricks CLI profile for this workspace is `enpu`.
- The `DEFAULT` Databricks profile is stale; use `--profile enpu`.

## Quick status

```bash
git status --short --branch
git log --oneline --decorate -5

gh auth status

databricks current-user me --profile enpu

databricks auth profiles
```

## Local environment

### Create or refresh the Python environment

```bash
uv venv --python 3.12
uv sync --all-groups
```

### Run checks

```bash
uv run --group dev python -m pytest
uv run --group dev ruff check .
uv run --group dev python -m mypy src tests
git diff --check
```

If `uv run pytest` or the `.venv/bin/mypy` script fails to spawn, call the module form
(`python -m ...`) or `.venv/bin/python -m pytest`. Everything at once:

```bash
uv run --group dev python -m pytest -q && uv run --group dev ruff check . \
  && uv run --group dev python -m mypy src tests && git diff --check
```

Run one test or one test file:

```bash
uv run --group dev python -m pytest tests/test_fixtures.py
uv run --group dev python -m pytest tests/test_fixtures.py -k decode -vv
```

### Inspect installed packages

```bash
uv tree --all-groups
uv pip list --python .venv/bin/python
```

## Environment variables

The local `.env` contains workspace credentials and is ignored by Git. Load it only
when a command needs it:

```bash
set -a
source .env
set +a
```

Check only whether required values are configured, without printing them:

```bash
env | awk -F= '/^(DATABRICKS_HOST|DATABRICKS_TOKEN)=/{print $1 "=configured"}'
```

Set values manually with placeholders:

```bash
export DATABRICKS_HOST="https://<workspace-host>"
export DATABRICKS_TOKEN="<token>"
```

Confirm the current host without printing the token:

```bash
printf 'Databricks host: %s\n' "$DATABRICKS_HOST"
test -n "$DATABRICKS_TOKEN" && echo "Databricks token: configured"
```

## GTFS-Realtime feeds

### Feed URLs

```bash
export KCM_VEHICLE_POSITIONS_URL="https://s3.amazonaws.com/kcm-alerts-realtime-prod/vehiclepositions.pb"
export KCM_TRIP_UPDATES_URL="https://s3.amazonaws.com/kcm-alerts-realtime-prod/tripupdates.pb"
export KCM_ALERTS_URL="https://s3.amazonaws.com/kcm-alerts-realtime-prod/alerts.pb"
```

### Download fixtures

```bash
mkdir -p tests/fixtures
curl -fsS -o tests/fixtures/vehiclepositions.pb "$KCM_VEHICLE_POSITIONS_URL"
curl -fsS -o tests/fixtures/tripupdates.pb "$KCM_TRIP_UPDATES_URL"
curl -fsS -o tests/fixtures/alerts.pb "$KCM_ALERTS_URL"
ls -lh tests/fixtures/*.pb
```

`-f` makes curl fail on HTTP errors and `-sS` keeps normal output quiet while
still showing errors.

### Check feed headers and entity counts

```bash
uv run --with gtfs-realtime-bindings --with protobuf python - <<'PY'
from pathlib import Path
from google.transit import gtfs_realtime_pb2

for name in ("vehiclepositions", "tripupdates", "alerts"):
    payload = Path(f"tests/fixtures/{name}.pb").read_bytes()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    print(name, "header_ts=", feed.header.timestamp, "entities=", len(feed.entity), "bytes=", len(payload))
PY
```

### Measure how often the source updates

Polls KCM directly (does not touch Databricks) and reports the feed-version spacing and
how often each bus reports a new position. Measured 2026-10-13: a new feed version about
every 21 seconds, a new fix per bus about every 16 seconds.

```bash
uv run python scripts/feed_cadence.py 130        # seconds to run
curl -sI "$KCM_VEHICLE_POSITIONS_URL" | grep -i 'last-modified\|etag\|content-length'
```

### Download two snapshots and compare timestamps

```bash
mkdir -p /tmp/transit-reliability-check
curl -fsS -o /tmp/transit-reliability-check/vehicle-1.pb "$KCM_VEHICLE_POSITIONS_URL"
uv run --with gtfs-realtime-bindings --with protobuf python - <<'PY'
from pathlib import Path
from google.transit import gtfs_realtime_pb2

feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(Path('/tmp/transit-reliability-check/vehicle-1.pb').read_bytes())
print(feed.header.timestamp)
PY
```

Repeat the download after a short interval and compare the header timestamp. Do not
use a shell `sleep` in long-running workflows; wait manually or use the collector's
scheduler once it exists.

## Databricks catalog and volumes

### Check authentication

```bash
databricks current-user me --profile enpu
databricks auth profiles
```

### List schemas

```bash
databricks schemas list workspace --profile enpu
```

### List expected volumes

The volume command takes separate catalog and schema arguments:

```bash
databricks volumes list workspace transit_landing --profile enpu
databricks volumes list workspace transit_ops --profile enpu
```

Expected volumes:

- `workspace.transit_landing.raw`
- `workspace.transit_ops.state`

### Create schemas and volumes in a Databricks SQL editor

```sql
CREATE SCHEMA IF NOT EXISTS workspace.transit_landing;
CREATE VOLUME IF NOT EXISTS workspace.transit_landing.raw;

CREATE SCHEMA IF NOT EXISTS workspace.transit_ops;
CREATE VOLUME IF NOT EXISTS workspace.transit_ops.state;
```

## Files API smoke test

### Upload a marker file

```bash
printf 'transit-reliability smoke test\n' >/tmp/transit-reliability-smoke.txt
curl -fsS -X PUT \
  "$DATABRICKS_HOST/api/2.0/fs/files/Volumes/workspace/transit_landing/raw/_smoke/hello.txt?overwrite=true" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  --data-binary @/tmp/transit-reliability-smoke.txt \
  -o /dev/null \
  -w 'upload_http=%{http_code}\n'
```

Expected upload status: `204` (or `200` in some API responses).

### Verify through the Databricks CLI

Use the `dbfs:/Volumes/...` URI form with the CLI:

```bash
databricks fs ls dbfs:/Volumes/workspace/transit_landing/raw/_smoke --profile enpu
databricks fs cat dbfs:/Volumes/workspace/transit_landing/raw/_smoke/hello.txt --profile enpu
```

### Verify through the Files API without displaying content

```bash
curl -fsS -I \
  "$DATABRICKS_HOST/api/2.0/fs/files/Volumes/workspace/transit_landing/raw/_smoke/hello.txt" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  | sed -n '1,8p'
```

Expected status: `200`.

## Uploading a raw feed file manually

This is useful before the collector exists or for a controlled smoke test. The file
name is intentionally timestamped so every snapshot remains immutable.

```bash
SOURCE="vehicle_positions"
DATE_UTC="$(date -u +%F)"
EPOCH_MS="$(python3 -c 'import time; print(int(time.time() * 1000))')"
LOCAL_FILE="tests/fixtures/vehiclepositions.pb"
REMOTE_PATH="/Volumes/workspace/transit_landing/raw/${SOURCE}/dt=${DATE_UTC}/${EPOCH_MS}.pb"

databricks fs cp "$LOCAL_FILE" "dbfs:$REMOTE_PATH" --overwrite --profile enpu

databricks fs ls "dbfs:/Volumes/workspace/transit_landing/raw/${SOURCE}/dt=${DATE_UTC}" --profile enpu
```

Do not use this as a substitute for the collector in production; it does not provide
scheduled polling, retry handling, or per-source health metrics.

## Databricks bundle commands

The bundle targets use the `enpu` profile (set in `databricks.yml`). Dev resources are
prefixed `[dev <user>]` and the dev schemas use the `dev_transit` prefix. The bundle
builds the wheel with `uv build`, so run these from the repository root after the local
checks pass.

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle summary -t dev
```

### Preview what a deploy would upload

```bash
databricks bundle sync -t dev --dry-run --full    # lists files that would sync; changes nothing
ls dist/ && python3 -c "import zipfile,glob; print(*zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]).namelist(), sep='\n')"
```

The second line shows exactly what is inside the wheel the job will install.

### Run jobs

```bash
databricks bundle run setup_job -t dev     # create schemas and the state volume
databricks bundle run ingest_job -t dev    # bronze Auto Loader, availableNow
```

The dev ingest schedule is `PAUSED` (prod runs every 30 minutes since 2026-10-14); `bundle run` still works. Expected
smoke result with the three fixtures landed: about 2 minutes first run, about 1 minute on
rerun, `TERMINATED SUCCESS`.

### Inspect runs

```bash
databricks jobs list --profile enpu
databricks jobs list-runs --job-id <job-id> --limit 5 --profile enpu
databricks jobs get-run <run-id> --profile enpu
databricks jobs get-run-output <task-run-id> --profile enpu   # traceback of a failed task
databricks jobs cancel-run <run-id> --profile enpu
```

`get-run-output` needs the task run id (under `tasks[].run_id` in `get-run`), not the job
run id. Pipe to `head -c 4000` if the output is large.

### Pause or unpause the schedule

Each job has its own switch in `databricks.yml`: `ingest_pause_status` (bronze) and
`silver_pause_status` (silver), set per target. Holding one while the other runs is normal, for
example holding silver during a rebuild.

Change `ingest_pause_status` (`PAUSED` or `UNPAUSED`) for the target in `databricks.yml`,
redeploy, and confirm the job's state:

```bash
databricks bundle deploy -t prod            # or -t dev
databricks jobs get <job-id> -o json --profile enpu \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["settings"]["schedule"])'
```

Pause both targets first whenever something looks wrong; collection keeps running and files
accumulate safely.

### Prod

```bash
databricks bundle deploy -t prod
databricks bundle run ingest_job -t prod
```

Prod has run on a 30-minute schedule (`0 0/30 * * * ?`, UTC) since 2026-10-14; dev is
paused. Deploying prod changes what the *next scheduled run* executes, so try the change in
dev first (`docs/databricks_guide.md`, section 6). `mode: production` needs
`workspace.root_path`, which is set in `databricks.yml`.

First-time prod setup (already done; for rebuilding from scratch):

```bash
databricks bundle validate -t prod
databricks bundle deploy -t prod
databricks bundle run setup_job -t prod      # schemas and state volume
databricks bundle run ingest_job -t prod     # first run backfills every landed file
```

The backfill took about 25 minutes for 1.5 days of files *(measured 2026-10-14)*; later
scheduled runs took about 110 seconds. Dev and prod use separate checkpoints
(`<schema_prefix>_bronze_<source>`) so they cannot steal each other's files.

### Tear down dev resources

```bash
databricks bundle destroy -t dev
```

This deletes the deployed dev jobs and bundle files. Tables, checkpoints, and landed
volume files are not removed.

## Daily ingest check (prod)

The five-minute routine; the reasoning is in `docs/databricks_guide.md`, section 7.

```bash
# 1. collection gaps (landed files)
uv run python scripts/collection_health.py

# 2. recent prod runs: expect SUCCESS and durations of a few minutes
PROD_JOB="$(databricks jobs list -o json --profile enpu | python3 -c '
import json,sys
print(next(j["job_id"] for j in json.load(sys.stdin) if "prod-ingest" in j["settings"]["name"]))')"
databricks jobs list-runs --job-id "$PROD_JOB" --limit 5 -o json --profile enpu | python3 -c '
import json,sys,datetime
d=json.load(sys.stdin); d=d if isinstance(d,list) else d.get("runs",[])
for r in d:
    t=datetime.datetime.fromtimestamp(r["start_time"]/1000,datetime.UTC).strftime("%m-%d %H:%M")
    print(t, r["state"]["life_cycle_state"], r["state"].get("result_state",""), (r.get("end_time",0)-r["start_time"])/1000,"s")'
```
```sql
-- 3. bronze is current and complete (compare files to what landed)
SELECT 'vehicle_positions' AS t, count(DISTINCT source_file) AS files,
       from_unixtime(max(feed_header_ts)) AS latest_feed
FROM workspace.transit_bronze.vehicle_positions
UNION ALL SELECT 'trip_updates', count(DISTINCT source_file), from_unixtime(max(feed_header_ts))
FROM workspace.transit_bronze.trip_updates
UNION ALL SELECT 'alerts', count(DISTINCT source_file), from_unixtime(max(feed_header_ts))
FROM workspace.transit_bronze.alerts;
```

Count the landed files for the same sources with `databricks fs ls ... | wc -l` per `dt=`
folder (the manual `*-smoke.pb` file counts as one per source). Bronze `files` should be
within the last 30 minutes of landing (about 90 vehicle, 30 trip-update, 6 alert files). A
run that says `SUCCESS` with far fewer files than landed is wrong; see the shared-checkpoint
case in the guide.

**Not every landed file has bronze rows.** KCM publishes an empty vehicle-positions feed for
about 11 minutes nightly (about 10:05 to 10:16 UTC; 15-byte files, zero entities), so about
35 landed vehicle files per day legitimately never appear as a `source_file`. When counts
differ, list the landed-but-absent files and look at their sizes: tiny files are the empty
window, large ones are a real problem. Also expect one `*-smoke.pb` source file per source
in prod bronze (the manual fixtures were ingested by the first backfill); silver must
exclude them.

## Static GTFS silver (schedule tables)

Built from the zips the GitHub workflow lands in `raw/gtfs_static/dt=<date>/`. Design:
`docs/decisions/0006-schedule-expansion-scope.md` and `0010-silver-trip-update-snapshots.md`.
The job `silver_job` has five tasks and runs daily at 11:45 UTC when unpaused:
`load_feeds` -> `expand_trips` -> `vehicle_positions`; `load_feeds` -> `expand_schedule`;
and `trip_updates` (independent). The silver build is incremental and idempotent, so it can
also be scheduled more often. What each silver table is and why: `docs/silver_tables.md`.
Verified in dev only; prod gets it at the next prod deploy.

```bash
databricks bundle deploy -t dev
databricks bundle run silver_job -t dev      # about 3 minutes the first time
```
```sql
-- feeds loaded (one row per schedule version)
SELECT * FROM workspace.dev_transit_silver.gtfs_feeds;
-- expanded service dates: rows should be about 449K and trips about 12.9K per weekday
SELECT service_date, count(*) AS rows, count(DISTINCT trip_id) AS trips
FROM workspace.dev_transit_silver.scheduled_stop_times GROUP BY 1 ORDER BY 1;
-- after-midnight check: GTFS 29:30:00 on service date D is 05:30 Pacific on D+1
SELECT s.trip_id, st.arrival_time, s.scheduled_arrival_ts,
       from_utc_timestamp(s.scheduled_arrival_ts, 'America/Los_Angeles') AS pacific
FROM workspace.dev_transit_silver.scheduled_stop_times s
JOIN workspace.dev_transit_silver.gtfs_stop_times st
  ON st.trip_id = s.trip_id AND st.stop_sequence = s.stop_sequence AND st.feed_version = s.feed_version
WHERE s.service_date = DATE'2026-10-14' ORDER BY s.scheduled_arrival_ts DESC LIMIT 1;
```

Silver vehicle positions and quarantine (verified in dev 2026-10-15):

```sql
-- quarantine rate (target: under about 2 percent) and the reasons
SELECT reason, count(*) FROM workspace.dev_transit_silver.quarantine_vehicle_positions GROUP BY 1;
-- schedule match rate by service date (NULL = date not expanded yet, not a mismatch)
SELECT service_date, count(*) AS fixes,
       round(100 * avg(CASE WHEN matched_trip THEN 1 ELSE 0 END), 2) AS match_pct,
       sum(CASE WHEN matched_trip IS NULL THEN 1 ELSE 0 END) AS unknown
FROM workspace.dev_transit_silver.vehicle_positions GROUP BY 1 ORDER BY 1;
-- no fix stored twice (both numbers must be equal)
SELECT count(*), count(DISTINCT vehicle_id, vehicle_ts) FROM workspace.dev_transit_silver.vehicle_positions;
```

Trip-update snapshots (decision 0010):

```sql
-- runs and snapshots for a service date; compare with a plain-SQL count from bronze
SELECT count(*) AS runs, sum(snapshots) AS snapshots
FROM workspace.dev_transit_silver.trip_update_snapshots WHERE service_date = DATE'2026-10-14';
-- one stop's history: when Metro changed its mind, and when a bus was assigned
SELECT first_seen, last_seen, arrival_time, arrival_delay, snapshots, vehicle_id
FROM workspace.dev_transit_silver.trip_update_snapshots
WHERE trip_id = '562518701' AND service_date = DATE'2026-10-14' AND stop_sequence = 18
ORDER BY first_seen;
-- (a prediction not seen for more than an hour ends its run, so compare with SQL that applies
--  the same rule: ts - lag(ts) > 3600 also starts a new run)
-- no unknown schedule matches, and trips exist through tomorrow
SELECT service_date, count(*) FROM workspace.dev_transit_silver.scheduled_trips GROUP BY 1 ORDER BY 1;
```

To test the incremental path without touching real tables, copy a slice of bronze into a
sandbox prefix and run the build in two stages: `build_silver_trip_updates --schema-prefix
<sandbox> --until-ts <midpoint epoch>`, then again without `--until-ts`; the result must
equal a single pass (use `EXCEPT` both ways). Procedure: "Test a bronze change in a sandbox".

To rebuild only the trip-update snapshots (needed when their rules change; done 2026-10-16):
pause the silver schedule (`silver_pause_status: PAUSED`, deploy), drop the table, then run just
that task. There is no checkpoint, because silver is not a streaming job:

```sql
DROP TABLE IF EXISTS workspace.transit_silver.trip_update_snapshots;
```
```bash
databricks bundle run silver_job -t prod --only trip_updates     # about 19 minutes for 3 days of data
```

Verify against independent SQL that applies the same rules before unpausing.

Stop visits (decision 0011). The task `stop_visits` runs after vehicle positions and the stop-level
schedule; the last two service dates are rebuilt each run, older ones once:

```bash
databricks bundle run silver_job -t prod --only stop_visits      # about 5 minutes for 3 dates
```
```sql
-- one row per scheduled stop: these two counts must be equal for each service date
SELECT service_date, count(*) FROM workspace.transit_silver.stop_visits GROUP BY 1 ORDER BY 1;
SELECT service_date, count(*) FROM workspace.transit_silver.scheduled_stop_times GROUP BY 1 ORDER BY 1;
-- method split (coverage)
SELECT arr_method, count(*), round(100 * count(*) / sum(count(*)) OVER (), 1)
FROM workspace.transit_silver.stop_visits WHERE service_date = DATE'2026-10-14' GROUP BY 1 ORDER BY 2 DESC;
-- invariants (all three must be 0)
WITH v AS (SELECT *, lag(arr_est) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS prev_arr
           FROM workspace.transit_silver.stop_visits WHERE service_date = DATE'2026-10-14')
SELECT sum(CASE WHEN arr_est < arr_lo OR arr_est > arr_hi THEN 1 ELSE 0 END),
       sum(CASE WHEN dep_est < arr_est THEN 1 ELSE 0 END),
       sum(CASE WHEN arr_est < prev_arr THEN 1 ELSE 0 END) FROM v;
-- one trip's visits
SELECT stop_sequence, stop_id, sched_arr, arr_est, arr_lo, arr_hi, arr_method, arr_delay_s, dep_est
FROM workspace.transit_silver.stop_visits WHERE service_date = DATE'2026-10-14' AND trip_id = '562518701'
ORDER BY stop_sequence;
```

Trip performance, the per-trip reliability table (decisions 0011 and 0012; reference: `docs/gold_tables.md`):

```bash
databricks bundle run silver_job -t prod --only trip_performance     # about 1 minute for 3 dates
```
```sql
-- one row per trip: compare with scheduled trips (counts must match per date)
SELECT service_date, count(*) FROM workspace.transit_gold.trip_performance GROUP BY 1 ORDER BY 1;
SELECT service_date, count(*) FROM workspace.transit_silver.scheduled_trips GROUP BY 1 ORDER BY 1;
-- the four categories add up to the timepoints that have an interval (difference must be 0 for every lens)
SELECT sum(n_common_early + n_common_on_time + n_common_late + n_common_uncertain) AS categorized
FROM workspace.transit_gold.trip_performance WHERE service_date = DATE'2026-10-14';
-- trip status
SELECT service_date, trip_status, count(*) FROM workspace.transit_gold.trip_performance GROUP BY 1, 2 ORDER BY 1, 3 DESC;
```

To rebuild silver vehicle positions from bronze, pause the job, drop `vehicle_positions` and
`quarantine_vehicle_positions` in that schema together (the watermark comes from them), and
rerun. Rebuild a feed or date: the loader deletes and re-inserts by `feed_version` (tables) or
`service_date` (expansion), so rerunning is safe. To force a reload of a feed, delete its
row from `gtfs_feeds` and rerun.

## Test a bronze change in a sandbox (do this before touching dev or prod tables)

A schema or parser change can corrupt a table that took hours to build. Test it on a tiny copy
first: a throwaway schema prefix, a small landing volume, and empty tables in the OLD schema.
Used for decision 0009.

```sql
CREATE SCHEMA IF NOT EXISTS workspace.evo_transit_bronze;
CREATE VOLUME IF NOT EXISTS workspace.transit_landing.evo_raw;
-- an empty table with the current (old) schema, so the new code must evolve it
CREATE TABLE workspace.evo_transit_bronze.trip_updates AS
  SELECT * FROM workspace.dev_transit_bronze.trip_updates LIMIT 0;
```
```bash
# land a few real files (fs cp does not create folders)
R=dbfs:/Volumes/workspace/transit_landing/evo_raw
databricks fs mkdir "$R/trip_updates/dt=2026-10-15" --profile enpu
databricks fs cp tripupdates.pb "$R/trip_updates/dt=2026-10-15/1792101309000.pb" --profile enpu
databricks bundle deploy -t dev          # builds and uploads the wheel with your change
# run the real ingest entry point once, against the sandbox, without touching any job
databricks jobs submit --no-wait --profile enpu --json '{
  "run_name": "sandbox-test",
  "tasks": [{"task_key": "bronze", "environment_key": "default",
    "python_wheel_task": {"package_name": "transit_reliability", "entry_point": "ingest_bronze",
      "parameters": ["--landing-volume", "workspace.transit_landing.evo_raw",
                     "--state-volume", "workspace.transit_ops.state",
                     "--schema-prefix", "evo_transit"]}}],
  "environments": [{"environment_key": "default", "spec": {"environment_version": "3",
    "dependencies": ["/Workspace/Users/<you>/.bundle/transit-reliability/dev/artifacts/.internal/transit_reliability-0.1.0-py3-none-any.whl"]}}]
}'
```

Then compare the table with an independent computation from the same file (here: row count
equals the feed's stop updates, and the two canceled trips have exactly 52 and 18 rows
flagged). Clean up afterwards: drop the sandbox schema and volume and delete its
checkpoints `.../state/checkpoints/evo_transit_bronze_*`.

## Bronze verification (SQL)

Run these in the Databricks SQL editor, or from the shell with the statement API. The
dev schema is `workspace.dev_transit_bronze`; prod is `workspace.transit_bronze`. The
queries below use prod; swap the schema for dev.

```sql
SELECT 'vehicle_positions' AS t, count(*) AS n FROM workspace.transit_bronze.vehicle_positions
UNION ALL SELECT 'trip_updates', count(*) FROM workspace.transit_bronze.trip_updates
UNION ALL SELECT 'alerts', count(*) FROM workspace.transit_bronze.alerts;
```

Smoke-fixture expectation (smoke files only): 522 / 33,772 / 81, unchanged after a rerun.

```sql
-- per-day volume, vehicles, and files
SELECT date(from_unixtime(feed_header_ts)) AS d, count(*) AS rows,
       count(DISTINCT vehicle_id) AS vehicles, count(DISTINCT source_file) AS files
FROM workspace.transit_bronze.vehicle_positions GROUP BY d ORDER BY d;

-- no file ingested twice (should return 0). Do not use (source_file, entity_id): the
-- feed itself repeats entity_ids within a file (placeholder rows), which silver removes.
SELECT count(*) FROM (
  SELECT source_file FROM workspace.transit_bronze.vehicle_positions
  GROUP BY source_file HAVING count(DISTINCT ingested_at) > 1);

-- Delta history (appends per micro-batch, with the job run that wrote each)
DESCRIBE HISTORY workspace.transit_bronze.vehicle_positions;
-- size and file count
DESCRIBE DETAIL workspace.transit_bronze.trip_updates;
```

### Test an incremental build with new data arriving between runs

Rerunning on identical data cannot expose a bug in an incremental build (decision 0015). Test it the
way it runs: build a sandbox copy of the *input* with only the first half of a time range, run the
build, append the second half to the input, run it again, and compare the output with an independent
plain-SQL count over the whole input. They must be equal. The sandbox recipe is the same as in "Test a
bronze change in a sandbox" (throwaway `evo_transit_*` schemas, `databricks jobs submit` with the dev
wheel); finish by dropping the sandbox schemas.

### Backfill and rebuild options

Silver vehicle positions: `build_silver_vehicle_positions --since '2026-10-13 00:00:00'` re-reads
bronze rows ingested after that time; the MERGE inserts only what is missing, so it is safe to repeat.
Downstream builders (`build_silver_stop_visits`, `build_gold_trip_performance`,
`build_gold_prediction_errors`, `build_gold_route_performance`, `build_gold_prediction_accuracy`) take
`--rebuild-from YYYY-MM-DD` to recompute service dates from that day on. A one-off run with these
parameters can be submitted as a multi-task `databricks jobs submit` run (see the 2026-10-18 repair in
decision 0015). The daily job ends with a `health_checks` task that fails when the output looks wrong.

### Run SQL from the shell

**Large results come back in pages.** The statement API returns the first page inline and the
rest through `next_chunk_index`; a script that reads only `result.data_array` silently gets a
fraction of the rows (one verification here read 114,599 of 295,554 and looked like a pipeline bug).
Compare the rows you got with `manifest.total_row_count`, and follow
`GET /api/2.0/sql/statements/<id>/result/chunks/<n>` until no `next_chunk_index` remains.


```bash
WAREHOUSE_ID="$(databricks warehouses list -o json --profile enpu \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
databricks api post /api/2.0/sql/statements --profile enpu --json "{
  \"warehouse_id\": \"$WAREHOUSE_ID\",
  \"wait_timeout\": \"50s\",
  \"statement\": \"SELECT count(*) FROM workspace.transit_bronze.alerts\"
}"
```

`databricks api` does not read the bundle profile; always pass `--profile enpu`.

### Re-land smoke files and force a reprocess

Checkpoints skip files already seen. To test a new file, upload it under a new name (see
"Uploading a raw feed file manually"). To rebuild a table from scratch, drop it and its
checkpoint together, otherwise the stream will think everything was processed (or, with a
checkpoint but no table, nothing will be written). Pause the target's schedule first, and
use the checkpoint for the same schema prefix (`dev_transit_...` or `transit_...`):

```bash
databricks fs rm -r dbfs:/Volumes/workspace/transit_ops/state/checkpoints/dev_transit_bronze_alerts --profile enpu
```
```sql
DROP TABLE IF EXISTS workspace.dev_transit_bronze.alerts;
```

### List landed raw files

```bash
for s in vehicle_positions trip_updates alerts; do
  databricks fs ls "dbfs:/Volumes/workspace/transit_landing/raw/$s" --profile enpu
done
```

## Collector (Cloudflare Worker)

Code lives in `workers/collector`. Run these from that directory. The architecture,
reasons, and data behavior are explained in `docs/collection_pipeline.md`.

### Local checks

```bash
cd workers/collector
npm ci
npm run typecheck
npm test
```

### One-time Cloudflare setup (interactive)

```bash
npx wrangler login           # opens a browser; or export CLOUDFLARE_API_TOKEN
npx wrangler whoami
```

### Secrets (never put these in `wrangler.toml`)

```bash
npx wrangler secret put DATABRICKS_HOST     # https://<workspace-host>, no trailing path
npx wrangler secret put DATABRICKS_TOKEN
npx wrangler secret list
```

### Deploy, watch, and test

```bash
npm run deploy                       # wrangler deploy
npm run tail                         # live logs, one JSON line per source per minute
npm run dev                          # local, with a scheduled-trigger endpoint
curl "http://localhost:8787/__scheduled?cron=*+*+*+*+*"   # fire a run while `npm run dev`
```

A healthy run logs `{"source":"vehicle_positions","ok":true,"bytes":...}`. Failures log
`ok:false` with an error and a `collect_failed` event.

### Verify files are landing

```bash
TODAY="$(date -u +%F)"
databricks fs ls "dbfs:/Volumes/workspace/transit_landing/raw/vehicle_positions/dt=$TODAY" --profile enpu | tail -5
databricks fs ls "dbfs:/Volumes/workspace/transit_landing/raw/vehicle_positions/dt=$TODAY" --profile enpu | wc -l
```

Expect about 60 files per hour for vehicle positions and trip updates, and about 12 per
hour for alerts. Count and gap check in SQL (exit criterion: no gap over 5 minutes):

```sql
WITH f AS (
  SELECT modificationTime AS t FROM read_files(
    '/Volumes/workspace/transit_landing/raw/vehicle_positions/', format => 'binaryFile')
)
SELECT count(*) AS files, min(t) AS first, max(t) AS last,
       max(gap_min) AS max_gap_minutes
FROM (SELECT t, (unix_timestamp(t) - unix_timestamp(lag(t) OVER (ORDER BY t))) / 60 AS gap_min FROM f);
```

Decode one landed file locally (replace the date and file name):

```bash
databricks fs cp "dbfs:/Volumes/workspace/transit_landing/raw/vehicle_positions/dt=<date>/<epoch_ms>.pb" /tmp/vp.pb --profile enpu
```

then use the "Check feed headers and entity counts" script above on `/tmp/vp.pb`.

### Analyze what has landed (lag, change per pull, gaps)

Download a day of one source and summarize it. Lines marked `<-- GAP` are more than 5
minutes after the previous file.

```bash
TODAY="$(date -u +%F)"; OUT="$(mktemp -d)"
for s in vehicle_positions trip_updates alerts; do
  mkdir -p "$OUT/$s"
  databricks fs cp -r "dbfs:/Volumes/workspace/transit_landing/raw/$s/dt=$TODAY" "$OUT/$s" --profile enpu
done
uv run python scripts/landing_report.py "$OUT"
```

Manual `*-smoke.pb` files are skipped automatically.

How many distinct feed versions did we actually capture versus files pulled? If much
lower, the feed updates slower than we poll:

```sql
SELECT count(DISTINCT feed_header_ts) AS distinct_snapshots,
       count(DISTINCT source_file)    AS files
FROM workspace.transit_bronze.vehicle_positions;   -- dev: workspace.dev_transit_bronze
```

### Worker secrets and logs in the dashboard

Cloudflare dashboard: Workers & Pages -> transit-collector -> Logs (observability is
enabled with persisted invocation logs).

### Token rotation

If uploads start returning 401/403 in `npm run tail`:

```bash
npx wrangler secret put DATABRICKS_TOKEN
```

Redeploy is not required for a secret change.

### Monitor cancelled or failed collection runs

A run killed for exceeding the 10 ms CPU limit, a failed deploy, or any outage leaves a
hole in the landed files. This checks the files themselves, so it works for any cause and
needs no Cloudflare analytics. It lists every gap over 90 s in trip updates and over 50 s
in vehicle positions (a deliberately skipped duplicate feed version leaves a ~40 s gap, so
smaller gaps are normal). For trip updates it then separates **lost** runs (minutes with no
file at all) from **delayed** runs (a gap of 90 to 100 s where every minute still has a
file). Cloudflare fires the cron up to about 40 s late at many hour and half-hour
boundaries, so a delayed run is normal and loses nothing; only empty minutes are lost data.
Vehicle-position gaps at those same boundaries are 50 to 70 s of missed sampling, not a
failed upload.

```bash
uv run python scripts/collection_health.py                 # today (UTC)
uv run python scripts/collection_health.py 2026-10-13 2026-10-14
```

Live view of Worker outcomes (anything other than `ok` is a failure, including an exceeded
CPU limit); run it for a while and count non-`ok` lines:

```bash
cd workers/collector
npx wrangler tail transit-collector --format json > /tmp/tail.json   # Ctrl-C when done
grep -c '"outcome": "ok"' /tmp/tail.json; grep '"outcome"' /tmp/tail.json | grep -vc '"ok"'
```

Known gaps: 2026-10-13 23:30 to 23:32 UTC (3 trip-update runs lost during a rolled-back
split-Worker attempt); 2026-10-14 02:45 to 02:51 and 2026-10-15 17:01 to 17:06 UTC (about 5
minutes each, whole Worker; cause not found); 2026-10-14 08:00 and 14:00 UTC (1 minute each). A Worker deploy during a run's 40 s wait also cancels that run, so
expect a single short gap at each deploy.

### Roll back or stop collection

```bash
npx wrangler rollback            # previous version
npx wrangler delete              # removes the Worker and its cron
```

## Static GTFS download (GitHub Actions)

`.github/workflows/daily_gtfs_static.yml` downloads KCM's schedule zip daily (11:17 UTC),
checks it is a real GTFS zip, and uploads it to
`/Volumes/workspace/transit_landing/raw/gtfs_static/dt=<date>/google_transit.zip` only when
its SHA-256 differs from `gtfs_static/latest.sha`. How it works: `docs/databricks_guide.md`.

### One-time setup (you do this; it involves a credential)

1. Create a **dedicated token**, separate from the Worker's, so you can revoke it alone. In
   the Databricks UI: your user menu, Settings, Developer, Access tokens, Generate new
   token. Comment `github-gtfs-static`, lifetime 90 days. Copy it once; it is not shown
   again. (The CLI can also create tokens, but it prints the value to the terminal; avoid it.)
2. Store it and the host as repo secrets. `gh secret set` prompts for the value and does not
   echo it:

```bash
gh secret set DATABRICKS_TOKEN            # paste the token when prompted
gh secret set DATABRICKS_HOST --body "https://<workspace-host>"
gh secret list                            # names only, never values
```

3. Scheduled workflows only run from the default branch, so push the workflow to `main`.

### Test it and check it

```bash
gh workflow run daily_gtfs_static.yml     # run now instead of waiting for the schedule
gh run list --workflow daily_gtfs_static.yml --limit 5
gh run view <run-id> --log                # every step's output
databricks fs ls dbfs:/Volumes/workspace/transit_landing/raw/gtfs_static --profile enpu
```

A second run on the same day should log "Feed unchanged" and upload nothing. Expect a new
`dt=` folder only after KCM publishes a new schedule version.

### When it fails

| Step that failed | Likely cause |
|---|---|
| Check secrets are configured | secret missing or misspelled |
| Check Databricks access | token expired or revoked (90-day lifetime) or wrong host: make a new token, `gh secret set DATABRICKS_TOKEN` |
| Download the feed | KCM unavailable; the next day's run retries |
| Reject anything that is not a real GTFS zip | KCM returned an HTML page or a truncated file; nothing is uploaded |

GitHub emails the repo owner when a scheduled run fails. Rotate the token before it expires
and note the expiry date in `docs/runbook.md`.

## CI

`.github/workflows/pr.yml` runs ruff, mypy, and pytest for Python and typecheck plus
vitest for the collector on every push to `main` and every pull request.

```bash
gh run list --limit 5
gh run watch
gh run view <run-id> --log-failed
```

## Limits and usage checks

Limits and our current usage are in `docs/limits.md`. Run these after any change to
schedules, and daily during the first week.

### Cloudflare CPU time per run (limit: 10 ms)

```bash
cd workers/collector
npx wrangler tail transit-collector --format json > /tmp/tail.json    # let it run a few minutes, then Ctrl-C
python3 - <<'PY'
import json
t = open("/tmp/tail.json").read(); dec = json.JSONDecoder(); i = 0
while (i := t.find("{", i)) >= 0:
    try: d, i = dec.raw_decode(t, i)
    except ValueError: i += 1; continue
    if "outcome" in d: print(d["outcome"], "cpu_ms=", d.get("cpuTime"), "wall_ms=", d.get("wallTime"))
PY
```

Anything at 10 ms, or an outcome other than `ok`, needs action (see `docs/limits.md` and
decision 0003). Expect `wall_ms` of about 43,000: the run waits between fetches on purpose.
Do not pipe `perl alarm` into the tail output; it buffers and loses the events.

### Databricks compute and storage (system tables, lag of hours)

```sql
SELECT usage_date, sku_name, round(sum(usage_quantity), 3) AS quantity, usage_unit
FROM system.billing.usage
GROUP BY ALL ORDER BY usage_date DESC LIMIT 20;
```

Run it in the SQL editor, or with the statement API shown under "Run SQL from the
shell". Record the daily DBU figure in `docs/runbook.md`.

### Which raw files are safe to delete (read-only)

Policy in `docs/decisions/0005-raw-retention-policy.md`. The report groups old landed
files into blocked (not in bronze), waiting for silver, and deletable. It never deletes:

```bash
uv run python scripts/raw_retention_report.py                        # trip_updates, 30 days
uv run python scripts/raw_retention_report.py --source alerts --keep-days 90
uv run python scripts/raw_retention_report.py --silver-verified-through 2026-11-01
```

Do not pass `--silver-verified-through` until the Phase 3 silver checks for those dates
have passed. Deleting folders is a separate manual step (`databricks fs rm -r ...`) that you
run yourself on the exact folders listed, after reading the counts.

### Size of the landing volume and bronze tables

```bash
databricks fs ls dbfs:/Volumes/workspace/transit_landing/raw/trip_updates --profile enpu
```
```sql
DESCRIBE DETAIL workspace.transit_bronze.trip_updates;   -- sizeInBytes, numFiles
```

## Git and GitHub

### Review changes

```bash
git status --short --branch
git diff
git diff --stat
git diff --check
```

### Commit and push

```bash
git add README.md docs/
git commit -m "docs: add command reference"
git push
```

Use a narrower `git add` command when unrelated changes are present. Never add `.env`.

### GitHub Actions

```bash
gh workflow list
gh run list --limit 10
gh run watch <run-id>
```

## Troubleshooting

### The Databricks profile fails

```bash
databricks auth profiles
databricks current-user me --profile enpu
```

Use `--profile enpu`; do not use the stale `DEFAULT` profile.

### A volume path cannot be found

Check the catalog objects first:

```bash
databricks schemas list workspace --profile enpu
databricks volumes list workspace transit_landing --profile enpu
databricks volumes list workspace transit_ops --profile enpu
databricks fs ls dbfs:/Volumes/workspace/transit_landing/raw --profile enpu
```

### A feed download fails

```bash
curl -v -o /tmp/feed.pb "$KCM_VEHICLE_POSITIONS_URL"
file /tmp/feed.pb
wc -c /tmp/feed.pb
```

A successful protobuf download should not be an HTML error page. Re-run the decode
check before adding the file to fixtures.

### Development tools cannot be spawned

Use Python module entry points:

```bash
uv sync --all-groups
uv run --group dev python -m pytest
uv run --group dev python -m mypy src tests
```

### Find ignored local files

```bash
git status --ignored --short | sed -n '1,40p'
git check-ignore -v .env
```

## To be added later

Keep future manual procedures in this document under a focused heading. Planned additions:

- Databricks SQL checks for silver, gold, and operations tables
- static GTFS download and verification
- backfill and replay procedures
- feed outage recovery
- data-quality and freshness checks
- production deployment and rollback

## SQL warehouse will not start ("The request could not be processed by the warehouse")

The SQL statement API (`databricks api post /api/2.0/sql/statements`) does not wake a stopped
warehouse. Check `databricks warehouses get <id> --profile enpu -o json` (field `state`) and start it
with `databricks warehouses start <id> --profile enpu`. Seen 2026-10-19: the start itself failed with
"Cannot create the resource, please try again later" while serverless job runs (prod ingest every
30 min) kept succeeding, so the cause is on the warehouse side, not the jobs. Cause not yet known
(unverified; a Free Edition capacity or quota limit is one possibility). Retry later, or start it
from the Databricks UI (SQL Warehouses) to read the full message.

## Test an inference change on one real day in a sandbox (used 2026-10-19, decision 0016 pending)

Goal: rebuild `stop_visits` for one service date with changed code and compare it with prod
without touching prod. The warehouse must be running (see "SQL warehouse will not start").

```sql
-- copy only what the build reads; one date of the big tables keeps it quick
CREATE SCHEMA IF NOT EXISTS workspace.evo_transit_silver;
CREATE OR REPLACE TABLE workspace.evo_transit_silver.gtfs_stop_times AS SELECT * FROM workspace.transit_silver.gtfs_stop_times;
-- same for gtfs_trips and gtfs_shapes, then the two date-partitioned inputs:
CREATE OR REPLACE TABLE workspace.evo_transit_silver.vehicle_positions AS
  SELECT * FROM workspace.transit_silver.vehicle_positions WHERE service_date = DATE'2026-10-16';
CREATE OR REPLACE TABLE workspace.evo_transit_silver.scheduled_stop_times AS
  SELECT * FROM workspace.transit_silver.scheduled_stop_times WHERE service_date = DATE'2026-10-16';
```
```bash
databricks bundle deploy -t dev --profile enpu     # uploads the wheel with your change (dev jobs stay paused)
# submit the real entry point once against the sandbox (JSON as in "Test a bronze change in a sandbox",
# entry_point build_silver_stop_visits, parameters ["--schema-prefix", "evo_transit"]); ~3.5 minutes
```
Compare `workspace.transit_silver.stop_visits` with `workspace.evo_transit_silver.stop_visits` on the same
date: interpolated arrivals with an interval over 180 s, number of rows by `arr_method`, median delay,
and a known trip row by row. Drop the `evo_transit_silver` schema afterwards.

## Two traps when checking collection by hand

- `databricks fs ls -l` prints each file's time in your LOCAL timezone but labels it `Z` (UTC). On
  2026-10-19 this made current files look seven hours old. File NAMES are epoch milliseconds and are
  reliable; convert them, or compare with `date -u`.
- A scheduled job that never starts leaves no run, so no failure email. On 2026-10-19 the prod ingest
  had no runs between 17:30 and 22:30 UTC, at the same time the SQL warehouse would not start (cause
  unverified). Look at the gap in `databricks jobs list-runs --job-id <id>`, not only at run results.
  `scripts/collection_health.py` also only sees gaps between landed files, not after the last one.

## Independent audit (read-only checks on prod)

Run the auditor's 28 SQL checks for one complete service date (at least 2 days old):

```bash
uv run --group dev python -m transit_reliability.audit.runners --profile enpu --day 2026-10-16
```
Each line is pass or FAIL with the measured value and the rule; the SQL for each check is in
`src/transit_reliability/audit/catalog.py` so you can paste it into the SQL editor and rerun it. The
warehouse must be running. After the 2026-10-19 rebuild: 24 pass, 4 fail (known, decision 0016).

## Rebuild stop visits and gold tables from a date (after an inference change)

Deploy the prod bundle first (the jobs run the deployed wheel), then submit one multi-task run: `build_silver_stop_visits`
`--rebuild-from D`, then `build_gold_trip_performance` and `build_gold_prediction_errors` (both after it),
then `build_gold_route_performance` (after trips) and `build_gold_prediction_accuracy` (after errors), each with
`--schema-prefix transit --rebuild-from D`, then `run_health_checks`. 2026-10-19 timings from 10-13: 770 s,
103, 156, 81, 83 s. `run_health_checks` fails with `silver_lag_hours` if silver vehicle positions are older
than 3 hours; rerun `build_silver_vehicle_positions` first (it is not part of this rebuild).

## Monitoring: how to inspect that everything works

You do not have to look anywhere for problems; look when you want proof.

**Alerts (nothing to do until one arrives).**
- Email from Databricks: a job failed. The subject names the job; open the run, read the error, which
  lists each failing check and its measured value.
- Email from GitHub ("watchdog" run failed): something is missing or unreachable. Open the run; the
  summary table says which check and why (for example "newest run started 330 min ago").

**See the state now (a few seconds, no warehouse needed):**
```bash
uv run python scripts/watchdog.py --profile enpu
```

**See the newest result of every check, and verify by eye (needs the SQL warehouse running):**
```bash
uv run python scripts/proof_report.py --profile enpu                 # saves docs/data_reports/proof_<day>.md
uv run python scripts/proof_report.py --profile enpu --seed second   # different random trips
```
Part 2 lists five random trips. Take a stop's location into a map, and ask: is a bus 600 ft away at the
time of the fix before and 150 ft away at the fix after consistent with an arrival between them?

**Ask the results table directly (SQL editor):**
```sql
-- failures and warnings in the last day
SELECT run_ts, suite, check_name, status, value, rule FROM workspace.transit_ops.check_results
WHERE status <> 'pass' AND run_ts > current_timestamp() - INTERVAL 1 DAY ORDER BY run_ts DESC;
-- trend of one check
SELECT run_ts, value FROM workspace.transit_ops.check_results
WHERE check_name = 'bronze_vehicle_positions_snapshots_in_a_full_hour' ORDER BY run_ts;
-- the exact SQL behind a check, to rerun by hand
SELECT DISTINCT sql FROM workspace.transit_ops.check_results WHERE check_name = '<name>';
```

**Run a suite by hand:** `databricks jobs run-now <monitor job id> --profile enpu` (hourly), or the
daily suite through a one-off submit of entry point `run_monitor` with `--suite daily [--day D]`.

**Statuses.** `pass`; `fail` (a rule broke; the job fails and emails); `warn` (a known limit, see
`KNOWN_LIMITS` in `audit/catalog.py` and decision 0016; recorded, never alerts).

## Transparency site (public page built from aggregates; decision 0018)

How it flows: `publish_site` task (hourly in the monitor job, daily in the silver job) -> tables in
`workspace.transit_public` -> `site.json` in the state volume -> GitHub workflow `publish-site` copies
that one file -> GitHub Pages. The page never queries Databricks.

```bash
# the file the page reads
databricks fs ls dbfs:/Volumes/workspace/transit_ops/state/public --profile enpu -l
databricks fs cp dbfs:/Volumes/workspace/transit_ops/state/public/site.json /tmp/site.json --profile enpu
python3 -c "import json;d=json.load(open('/tmp/site.json'));print(d['generated_at'], d['errors'], {k:len(v) for k,v in d['tables'].items()})"

# refresh now instead of waiting: run the monitor job (hourly sections) or the silver job (all sections)
databricks jobs run-now <monitor job id> --profile enpu

# preview the page locally with that file
mkdir -p site/data && cp /tmp/site.json site/data/site.json && cd site && python3 -m http.server 8000
# then open http://localhost:8000   (site/data/ is git-ignored)

# publish by hand / see the last runs
gh workflow run publish_site.yml ; gh run list --workflow publish_site.yml --limit 3
```
One-time setup: repository Settings -> Pages -> Source: "GitHub Actions" (or
`gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow`). The page then lives at
`https://<owner>.github.io/<repo>/` (here: https://enpuyou.github.io/transit-reliability/, live since 2026-10-20).

Time zone: the page shows Seattle time everywhere (see decision 0018); raw JSON fields are UTC.
Freshness: `data/site.json` is rebuilt every 3 hours (the monitor job runs hourly but its `publish_live` task
exits at once except in UTC hours 0, 3, 6, ...; `--every-hours 3`; stale after 4.5 hours) and `data/status.json` every 15 minutes (workflow
`publish_site.yml`, via `scripts/watchdog.py --json-out`). Run the probes yourself, locally:
`uv run python scripts/watchdog.py --profile enpu --json-out site/data/status.json`. To see the page's
failure messages, edit `checked_at` in `site/data/status.json` to two hours ago (stale probes) or set a
finding's `ok` to false, then reload the local preview. Charts need `site/vendor/plotly-basic.min.js`
(vendored; upgrade by downloading a newer `plotly.js-basic-dist-min` build and checking the page).

If a section on the page says it is unavailable, `errors` in `site.json` names the query; rerun that
query from `src/transit_reliability/public/queries.py` in the SQL editor. If the workflow goes red with
"site data is N h old", the monitor job is not running: see "Monitoring" in `docs/runbook.md`.
To see the public tables themselves: `SHOW TABLES IN workspace.transit_public`.


## Dispatcher Worker (alarm clock for GitHub workflows; decision 0020)

GitHub's own `schedule` trigger is unreliable here (measured 2026-10-20: 2 of about 64 expected
15-minute runs, the daily job 3 to 6 hours late). The Worker `workers/dispatcher` asks GitHub to run
`publish_site.yml` every 15 minutes and `watchdog.yml` hourly at :05; the work itself stays in GitHub.

**One-time setup (the owner does the token steps; never paste the token into chat or a file):**
1. github.com -> Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens ->
   Generate. Repository access: Only select repositories -> this repository. Permissions -> Repository
   permissions -> **Actions: Read and write** (Metadata read is added automatically). Expiry: the longest
   offered; put the date in `docs/runbook.md`.
2. `cd workers/dispatcher && npm install && npx wrangler secret put GITHUB_TOKEN --name transit-dispatcher`
   and paste the token at the prompt (wrangler uses the Cloudflare login already used for the collector).
   Always give `--name`: without it wrangler picks the Worker from the current folder's wrangler.toml, and
   a token put from the wrong folder lands on another Worker (happened 2026-10-20). Check where a secret
   is with `npx wrangler secret list --name <worker>` (names only).
3. `npm run deploy`.

**Check that it fires (do this before trusting it):**
```bash
gh run list --workflow publish_site.yml --event workflow_dispatch --limit 6   # a run at each :00 :15 :30 :45
gh run list --workflow watchdog.yml --event workflow_dispatch --limit 3        # one at :05 each hour
cd workers/dispatcher && npx wrangler tail transit-dispatcher                  # live log lines (status 204 = accepted)
```
Cloudflare dashboard: Workers & Pages -> transit-dispatcher -> Logs. A failed dispatch (status 401/403:
token missing, expired or without Actions permission; 404: wrong workflow file name or repo) throws, so
the invocation is shown as failed. Rotate the token with `npx wrangler secret put GITHUB_TOKEN --name transit-dispatcher`.
`npm run typecheck && npm test` in `workers/dispatcher` run in CI with the other checks.

## Regenerate the website's worked example (arrival and departure explainer)

The "How we work out when a bus arrived and left" section of the website is drawn from `site/example.json`:
one real trip (route 49, 2026-10-16) worked through step by step. Regenerate it, for example after the
inference rules change, with the SQL warehouse running:

```bash
uv run python scripts/make_site_example.py --profile enpu          # writes site/example.json
# the last lines print the hand-calculated estimates next to the values stored by the pipeline: they must match
cd site && python3 -m http.server 8000                              # preview at http://localhost:8000
```
The script runs the pipeline's own functions (`arrivals/infer.py`) on that trip's real route line, stops
and GPS reports, so the page never drifts from the code. The file has no vehicle id; positions appear as
distances along the route plus the route line's own coordinates (public timetable data). To explain a
different trip use `--trip <trip_id> --day <date>` (the trip needs a middle timepoint with interpolated
arrival and departure). The rendering code is `site/explainer.js`.

## Weather data (decision 0024)

```bash
python3 scripts/fetch_weather.py --hours 6 --out /tmp/weather_out    # fetch locally and look at the files
gh workflow run weather.yml -f hours=170                              # backfill the last week into the landing volume
gh run list --workflow weather.yml --limit 5                          # the dispatcher starts it hourly at :20
databricks fs ls dbfs:/Volumes/workspace/transit_landing/raw/weather/dt=$(date -u +%F) --profile enpu
```
```sql
-- is weather arriving? (newest reading and stations reporting)
SELECT station, max(observed_ts), count(*) FROM workspace.transit_silver.weather_observations GROUP BY 1;
-- hours by class and the rainy ones
SELECT weather_class, count(*) FROM workspace.transit_silver.weather_hourly WHERE n_obs >= 6 GROUP BY 1;
SELECT * FROM workspace.transit_gold.weather_impact ORDER BY weather_class, day_type, band;
```
The silver job parses new files daily (task `weather`) and rebuilds `weather_impact` (task `weather_impact`).

## Live bus map (decision 0025)

The page `site/map.html` asks the relay Worker (`workers/relay`) for the agency's bus file every 20
seconds and draws the buses. Nothing here touches Databricks except the route-number file.

```bash
# 1. test the relay Worker's code
cd workers/relay && npm install && npm run typecheck && npm test

# 2. run the relay on your machine and look at what it returns
npx wrangler dev --port 8787            # leave running; Ctrl-C to stop
curl -s -D - -o /tmp/veh.pb http://localhost:8787/vehicles   # expect 200, access-control-allow-origin: *, ~50 KB
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/secret   # expect 404 (only listed paths are served)

# 3. check the page's feed reader against the Python parser (same sample file)
node --test site/test/gtfsrt.test.mjs site/test/heading.test.mjs

# 4. look at the page locally. The page works with the live relay as it is, so step 2 is optional: open
#    http://localhost:8000/map.html . Edit site/map.js or site/map.html and reload the page (Cmd-Shift-R)
#    to see the change; there is no build step. Add ?marker=pointer|badge|beam|chevron|drop to try a bus marker style (compare them all at
#    http://localhost:8000/marker-options.html). Add ?base=none to skip the background map (used for
#    automated screenshots). Add ?relay=http://localhost:8787 to use your own local relay from step 2.
cd site && python3 -m http.server 8000     # then open http://localhost:8000/map.html?relay=http://localhost:8787

# 5. refresh the map's data files: route numbers, stop names, destination names (headsigns.json), route lines (shapes.json), route stops (route_stops.json), and a local
#    copy of the typical-lateness history
#    (needs the SQL warehouse running; the history should be refreshed daily, by hand for now)
uv run python scripts/make_map_data.py --profile enpu
```

What to look for in step 4: thin route lines under the dots (click a bus: its whole route turns bold; click empty map to clear), arrows over Seattle (each pointing the way its bus is heading) that glide to new positions every 10 seconds without you touching the page, an open popup that follows its bus, a green "Live: agency feed from <time>" banner, a route
picker filled with route numbers, a popup when you click a dot. If the banner is red, the page cannot
reach the relay (check step 2 is running and the address after `?relay=`). The relay is deployed at
`https://transit-relay.youenpu.workers.dev` (2026-10-21). Check it from outside:
`curl -s -D - -o /dev/null https://transit-relay.youenpu.workers.dev/vehicles` should show 200,
`access-control-allow-origin: *` and, on a second call within 15 s, `cf-cache-status: HIT`. Redeploy after
changing it: `cd workers/relay && npx wrangler deploy`. Watch it: `npm run tail` there.
