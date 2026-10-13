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

### Run jobs

```bash
databricks bundle run setup_job -t dev     # create schemas and the state volume
databricks bundle run ingest_job -t dev    # bronze Auto Loader, availableNow
```

The dev ingest schedule is deliberately `PAUSED`; `bundle run` still works. Expected
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

Change `ingest_pause_status` for the target in `databricks.yml` and redeploy:

```bash
databricks bundle deploy -t dev
```

### Prod

```bash
databricks bundle deploy -t prod
databricks bundle run ingest_job -t prod
```

Do not deploy to `prod` until the dev job has been run and its manual verification
checks are recorded in the setup notes or runbook. Prod is unpaused with a 30-minute
schedule, so deploying it starts spending compute.

### Tear down dev resources

```bash
databricks bundle destroy -t dev
```

This deletes the deployed dev jobs and bundle files. Tables, checkpoints, and landed
volume files are not removed.

## Bronze verification (SQL)

Run these in the Databricks SQL editor, or from the shell with the statement API. The
dev schema is `workspace.dev_transit_bronze`; prod is `workspace.transit_bronze`.

```sql
SELECT 'vehicle_positions' AS t, count(*) AS n FROM workspace.dev_transit_bronze.vehicle_positions
UNION ALL SELECT 'trip_updates', count(*) FROM workspace.dev_transit_bronze.trip_updates
UNION ALL SELECT 'alerts', count(*) FROM workspace.dev_transit_bronze.alerts;
```

Smoke-fixture expectation: 522 / 33,772 / 81, unchanged after a rerun.

```sql
-- per-day volume, vehicles, and files
SELECT date(from_unixtime(feed_header_ts)) AS d, count(*) AS rows,
       count(DISTINCT vehicle_id) AS vehicles, count(DISTINCT source_file) AS files
FROM workspace.dev_transit_bronze.vehicle_positions GROUP BY d ORDER BY d;

-- duplicate check (should return no rows)
SELECT source_file, entity_id, count(*) AS n
FROM workspace.dev_transit_bronze.vehicle_positions
GROUP BY source_file, entity_id HAVING n > 1 LIMIT 10;

-- Delta history (appends per micro-batch)
DESCRIBE HISTORY workspace.dev_transit_bronze.vehicle_positions;
```

### Run SQL from the shell

```bash
WAREHOUSE_ID="$(databricks warehouses list -o json --profile enpu \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
databricks api post /api/2.0/sql/statements --profile enpu --json "{
  \"warehouse_id\": \"$WAREHOUSE_ID\",
  \"wait_timeout\": \"50s\",
  \"statement\": \"SELECT count(*) FROM workspace.dev_transit_bronze.alerts\"
}"
```

`databricks api` does not read the bundle profile; always pass `--profile enpu`.

### Re-land smoke files and force a reprocess

Checkpoints skip files already seen. To test a new file, upload it under a new name (see
"Uploading a raw feed file manually"). To rebuild a table from scratch, drop it and its
checkpoint together, otherwise the stream will think everything was processed:

```bash
databricks fs rm -r dbfs:/Volumes/workspace/transit_ops/state/checkpoints/bronze_alerts --profile enpu
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

- Cloudflare Wrangler login, secrets, local Worker development, and deployment
- collector cron and tail commands
- Databricks SQL checks for silver, gold, and operations tables
- static GTFS download and verification
- backfill and replay procedures
- feed outage recovery
- data-quality and freshness checks
- production deployment and rollback
