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

### Run SQL from the shell

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
smaller gaps are normal), and estimates how many runs or fetches were lost.

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
split-Worker attempt). A Worker deploy during a run's 40 s wait also cancels that run, so
expect a single short gap at each deploy.

### Roll back or stop collection

```bash
npx wrangler rollback            # previous version
npx wrangler delete              # removes the Worker and its cron
```

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
