# Working with Databricks: a hands-on guide for this project

Goal of this document: after reading it you can deploy, run, check, debug, and repair
this project's Databricks side **without an AI agent**, and you know why each step exists.
It uses this repository's real setup and the real failures we hit on 2026-10-13/14.

- Exact commands to copy: `docs/commands.md`.
- How the data gets here from King County Metro: `docs/collection_pipeline.md`.
- Why we chose things: `docs/decisions/`. Limits: `docs/limits.md`.

Statements marked *(observed)* were seen on this workspace; anything else is general
Databricks behavior you should confirm in the official docs when it matters.

---

## 1. The whole system in one picture

```
KCM S3 feed --GET--> Cloudflare Worker --PUT (Files API)--> Volume (raw .pb files)
                      (cron, every minute)                      |
                                                                | Auto Loader, every 30 min
                                                                v
                                       Databricks job (serverless) --> Delta tables (bronze)
```

Two independent halves joined only by files in a volume:

| Half | Runs on | Cadence | If it breaks |
|---|---|---|---|
| Collect | Cloudflare Worker | every minute | data for that minute is gone forever (the source overwrites itself) |
| Ingest | Databricks job | every 30 min | nothing is lost; the next run catches up from the files |

That asymmetry is the most important design fact. **Collection is the irreplaceable
part; ingest can always be re-run.** When something goes wrong, first ask which half.

## 2. Databricks concepts you need

### Workspace, catalog, schema, objects (Unity Catalog)

Everything addressable lives under `catalog.schema.object`:

```
workspace                         <- catalog (Free Edition gives you one, named "workspace")
  transit_landing                 <- schema
    raw                           <- VOLUME: a folder of plain files
  transit_ops
    state                         <- VOLUME: checkpoints live here
  transit_bronze                  <- schema (prod)
    vehicle_positions             <- TABLE (Delta)
  dev_transit_bronze              <- schema (dev copy)
```

- A **volume** is file storage (our raw `.pb` files, Spark checkpoints). You address it as
  a *path*: `/Volumes/workspace/transit_landing/raw/...`.
- A **table** is a Delta table you query with SQL: `workspace.transit_bronze.trip_updates`.
- **Dotted name vs path.** `workspace.transit_landing.raw` is a *name*;
  `/Volumes/workspace/transit_landing/raw` is a *path*. Mixing them up was our first real
  bug (the ingest looked in a folder that did not exist). Always build paths with
  `bronze.schema.volume_path()` (CLAUDE.md "Never" list).

### Delta tables

A Delta table is Parquet files plus a transaction log. Each write is an atomic commit
(a "version"). Useful consequences:

- `DESCRIBE HISTORY <table>` shows every commit, who wrote it, which job run, rows written.
  This is how you prove *when* and *by what* data arrived.
- Appends never rewrite old data, so bronze is cheap and safe to keep.
- `DESCRIBE DETAIL <table>` shows size and file count. *(observed 2026-10-14: 36M
  trip-update rows are only about 146 MB, far smaller than the raw files.)*

### Compute

- **Serverless job compute.** Our jobs run on serverless: you do not manage clusters.
  It is **Spark Connect**: the client sends a query plan to a remote Spark. Consequences
  we hit: no `rdd`, no `createDataFrame(rdd)`; use DataFrame APIs and `mapInPandas`.
- **SQL warehouse.** A small always-available SQL endpoint (one 2X-Small on Free Edition).
  It is for *you* to run queries and for the statement API. Jobs do not use it.
  Warehouse queries also consume quota (*observed*: manual checking used more DBU than the
  ingest job on 2026-10-13).
- **Free Edition limits.** Quotas are unpublished; an overrun stops compute for the day.
  So we measure (section 9) and keep the schedule coarse (30 minutes).

### Job, task, run

- A **job** is a definition (what to run, on what compute, on what schedule).
- A **task** is one step in a job. Ours has one: `bronze`.
- A **run** is one execution. States: `PENDING/RUNNING` -> `TERMINATED` with a result
  `SUCCESS` or `FAILED` (or `INTERNAL_ERROR` when the platform reports a failure).
  A run's tasks have their *own* run ids; you need the **task** run id to read errors.

## 3. Tools and authentication

| Tool | Used for |
|---|---|
| `databricks` CLI | bundles, jobs, listing/copying files, raw API calls |
| `uv run python -m pytest ...` | local tests (no Databricks needed) |
| SQL editor in the browser | quick queries, exploring tables |
| statement API (`databricks api post /api/2.0/sql/statements`) | run SQL from a terminal |
| Files API (what the Worker uses) | upload bytes to a volume over HTTPS |

**Profiles.** The CLI reads `~/.databrickscfg`. This project uses the profile `enpu`.
The `DEFAULT` profile is stale. Forgetting `--profile enpu` is the most common
"it says unauthorized / wrong workspace" cause. Note `databricks api` ignores the
bundle's profile setting, so always pass `--profile enpu` to it.

**Secrets.** A personal access token lets the Worker upload files. It lives only as a
Cloudflare Worker secret and in your local `.env` (ignored by git). Never paste it into
a command line, doc, or commit. If uploads start failing with 401/403, rotate it
(`docs/commands.md`, "Token rotation").

## 4. Declarative deployment: Databricks Asset Bundles

The files that define the Databricks side:

| File | Defines |
|---|---|
| `databricks.yml` | the bundle: variables, how to build the Python wheel, and the **targets** `dev` and `prod` |
| `resources/setup_job.yml` | job that creates schemas and the state volume |
| `resources/ingest_job.yml` | job that runs bronze ingest on a 30-minute cron |
| `pyproject.toml` | the Python package (`transit_reliability`) and its entry points |

### The mental model

A bundle is "infrastructure as code" for Databricks. You edit YAML and Python locally;
`bundle deploy` makes the workspace match. You should rarely click things in the UI,
because the next deploy overwrites UI edits.

Three verbs:

| Command | What it does | Spends compute? |
|---|---|---|
| `databricks bundle validate -t <target>` | checks the config | no |
| `databricks bundle deploy -t <target>` | builds the wheel, uploads files, creates/updates jobs | no (unless a schedule fires) |
| `databricks bundle run <job> -t <target>` | starts a job run now | **yes** |

**Deploy changes what the schedule runs.** The scheduled job always uses the last
*deployed* wheel. So a deploy of broken code breaks the next scheduled run. This is the
main reason prod exists (section 5).

### What actually gets deployed, and how the job uses it

`bundle deploy` does three separate things. Knowing which one matters answers most
"why didn't my change take effect?" questions.

| Step | What | Where it ends up | Used by the job? |
|---|---|---|---|
| 1. Build | `uv build --wheel` packages `src/transit_reliability` into `dist/*.whl` | local `dist/` | no, just the product |
| 2. Upload the wheel | the wheel file | `<root>/artifacts/.internal/` | **yes**, this is what runs |
| 3. Sync files | repo files not excluded by `.gitignore` or `sync.exclude` | `<root>/files/` | no, a convenience copy |

The wheel contains only the package: `bronze/`, `entrypoints/`, `gtfs_rt/`, `retention.py`
and the entry-point metadata. Tests, scripts, docs, and the Worker are not in it.

**The job installs the wheel on every run.** The job's `environments` block lists the wheel
as a dependency. Serverless compute has no long-lived machine, so each run builds its
environment from that spec (base image `environment_version: "3"`, plus our wheel, plus
`gtfs-realtime-bindings` and `protobuf` from `pyproject.toml`), then calls the entry point
(`ingest_bronze`, mapped in `[project.scripts]`). You cannot "install once" on serverless;
a classic cluster could keep libraries installed but would sit idle or restart each run,
and Free Edition offers serverless only. The side benefit is that every run uses exactly the
deployed code. We have not measured how much of a run's ~110 s is environment setup (the
run reports a 2 s setup phase, but whether the install is inside it is unverified).

**Consequences**
- Editing `src/` changes nothing in Databricks until the next `bundle deploy` rebuilds the
  wheel. The schedule always runs the last deployed wheel.
- The wheel version is always `0.1.0`, so you cannot tell deployed versions apart by name;
  check `git log` and the deploy time. Bump the version if that becomes confusing.
- `sync.exclude` in `databricks.yml` keeps docs, tests, scripts, and the Worker out of the
  workspace copy. *(Configured 2026-10-14. Dev deploy
  verified the same day: 39 files removed from the workspace copy, 2 uploaded, and the dev
  setup job still ran `SUCCESS` from the wheel. Prod not deployed with it yet; it applies
  at the next prod deploy.)*

### Reading `databricks.yml`

| Block | What it does |
|---|---|
| `bundle: name` | project name; appears in deploy paths (`.bundle/transit-reliability/...`) |
| `include: resources/*.yml` | pulls in the job definitions, so this file holds settings and `resources/` holds work |
| `sync: exclude` | files not to upload to the workspace copy (see above) |
| `artifacts: default` | how to build the deployable: `uv build --wheel`, project at `.` |
| `variables` | named settings with defaults (`schema_prefix`, `landing_volume`, `state_volume`, `ingest_pause_status`), used in jobs as `${var.name}` |
| `targets: dev / prod` | named environments that override variables; chosen with `-t` |

Inside `targets`: `mode: development` prefixes resource names with `[dev <you>]`;
`mode: production` is stricter and requires `workspace.root_path`. `workspace.profile`
picks the CLI login. `${bundle.name}` and `${bundle.target}` are filled in at deploy time.

Flow: `-t prod` selects the target, which sets the variables, which fill the job templates in
`resources/*.yml` (for example the schedule's pause status and the `--schema-prefix`
parameter), and `bundle deploy` builds the wheel and makes the workspace match. In the job
files, the dependency `../dist/*.whl` is rewritten at deploy time to the uploaded wheel's
workspace path.

### Targets: dev vs prod

| | `dev` | `prod` |
|---|---|---|
| bundle `mode` | `development` | `production` |
| schema prefix | `dev_transit` -> `dev_transit_bronze` | `transit` -> `transit_bronze` |
| job name | `[dev youenpu] transit-reliability-dev-ingest` | `transit-reliability-prod-ingest` |
| where files are deployed | per-user dev path (auto) | `root_path` we must set explicitly |
| schedule now | **paused** | **every 30 minutes** |
| checkpoint | `.../checkpoints/dev_transit_bronze_<source>` | `.../checkpoints/transit_bronze_<source>` |

`mode: production` refuses to validate without a `workspace.root_path` (a guard so only
one copy is deployed). We hit that error *(observed)*; the fix is in `databricks.yml`.

Both targets read the **same** landing volume and write to **different** schemas.
The pause switch is the variable `ingest_pause_status`, set per target.

**Why prod matters here.** In one workspace dev and prod are the same compute and code.
The real differences are table names, deploy path, and that prod only changes when you
deliberately deploy it. Use dev to try things; keep prod boring.

## 5. How the ingest works (read `src/transit_reliability/bronze/streaming.py` alongside)

1. The job's entry point is `transit_reliability.entrypoints.jobs:ingest_bronze`,
   registered in `pyproject.toml`. The job installs our wheel and calls it.
2. For each source it starts a **streaming query** with **Auto Loader**
   (`cloudFiles`, format `binaryFile`): it lists the volume folder and yields only files
   it has not seen. "Seen" is remembered in a **checkpoint** (a folder in the state volume).
3. `trigger(availableNow=True)`: process everything pending, then stop. So the job is a
   *batch job built on a streaming engine*: cheap, and it catches up after outages.
4. `foreachBatch` hands each micro-batch to our function, which parses the protobuf bytes
   with pure Python (`gtfs_rt/parse.py`) via `mapInPandas`, adds `ingested_at`, and appends
   to Delta. `txnAppId`/`txnVersion` make a retried batch an idempotent no-op.
5. `cloudFiles.maxFilesPerTrigger` caps files per micro-batch (trip updates: 50). Without
   it the first run after a long gap tried to parse the whole backlog in one Python worker
   and died *(observed)*.

### Checkpoints: the one concept to master

A checkpoint is the stream's memory of "which files I have already processed". Rules that
follow:

- **Table and checkpoint are a pair.** Delete one without the other and you get missing
  or duplicated data. To rebuild a table: drop the table **and** delete its checkpoint.
- **Never share a checkpoint between two jobs.** We did, between dev and prod *(observed
  2026-10-14)*: prod saw only files newer than dev's last run, and finished "successfully"
  in 83 seconds with 10 files instead of ~3,900. The path now includes the schema prefix.
- **A new checkpoint re-reads every file in the folder** (we set
  `includeExistingFiles=true`). That is how the prod backfill worked.
- Raw files are the source of truth, so a lost checkpoint is recoverable; a lost *raw
  file* is not. Never delete raw files until downstream tables are verified.

## 6. The development loop (what to do for any change)

```
 edit code
   |
   v  1. local checks  (pytest, ruff, mypy)            -- seconds, free
   v  2. bundle validate -t dev                         -- checks YAML
   v  3. bundle deploy -t dev                           -- uploads the wheel
   v  4. bundle run ingest_job -t dev                   -- minutes of compute
   v  5. verify in SQL (counts, duplicates, history)    -- never trust SUCCESS alone
   v  6. commit
   v  7. bundle deploy -t prod  (deliberate)            -- changes what the schedule runs
   v  8. watch the next scheduled prod run + verify
```

Why each step: tests catch logic errors for free; `validate` catches config errors without
a deploy; dev proves the change on real data away from prod's tables; SQL verification
catches the "successful but wrong" case that the shared-checkpoint bug produced; deploying
prod is the only step that changes live behavior, so it is last and explicit.

**Verification is part of the job.** A run state of `SUCCESS` only means no exception.
Always compare against the source of truth: bronze distinct `source_file` count vs files
landed in the volume, and `count(distinct ingested_at)` per file must be 1 (no file
processed twice). Queries are in `docs/commands.md`.

## 7. Operating routine (5 minutes, daily at first)

1. **Is collection healthy?** `uv run python scripts/collection_health.py` (lists gaps in
   landed files). A gap here is lost data; investigate Cloudflare first.
2. **Are ingest runs healthy?** `databricks jobs list-runs --job-id <prod id>`: all
   `SUCCESS`, durations of a few minutes (*observed*: ~110 s). A run approaching 30
   minutes means runs will start overlapping/skipping.
3. **Is bronze current?** latest `feed_header_ts` in bronze within ~30 minutes of now;
   distinct `source_file` close to the landed file count.
4. **What is it costing?** `system.billing.usage` (lags by hours), recorded in
   `docs/runbook.md`.
5. **Is storage OK?** `DESCRIBE DETAIL` on the tables; sizes of the landing folders.

## 8. Debugging playbook

### Reading a failed run

```
databricks jobs list-runs --job-id <id> --limit 5 --profile enpu     # find the run
databricks jobs get-run <run-id> --profile enpu                       # state + tasks[].run_id
databricks jobs get-run-output <TASK-run-id> --profile enpu           # the traceback
```

The job-level message is just "Workload failed". The real exception is in the **task**
output. It is long and full of terminal color codes; look for the last `Traceback` and
the first line naming the exception. If it is a streaming failure, the useful line is the
`Caused by:`.

### Failures we actually had (use these as pattern recognition)

| Symptom | Cause | Fix |
|---|---|---|
| ingest finds no files / "path not found" | built `/Volumes/{workspace.transit_landing.raw}` with dots | `volume_path()` converts dots to slashes |
| `rdd` / `createDataFrame(rdd)` error | serverless is Spark Connect | `mapInPandas` inside `foreachBatch` |
| optional ints become `NaN` floats | pandas default for missing ints | nullable `Int64` |
| `Connection reset` from the Python worker on trip updates | first batch tried to parse the entire backlog in memory | `maxFilesPerTrigger` + yield one frame per file |
| prod run `SUCCESS` but nearly empty tables, `alerts` table missing | prod and dev shared a checkpoint | per-prefix checkpoint path; drop tables; backfill |
| GitHub workflow failed at upload: `no such directory` | `databricks fs cp` does not create missing folders | run `databricks fs mkdir` first (it creates parents and is idempotent) |
| expired token reported as "file does not exist" | `fs cp` of a missing file hides the auth error | check access with `fs ls` on a folder that always exists before relying on `fs cp` |
| first run took ~25 min with a long silent start | big backlog plus serverless startup/listing *(cause of the ~11 min delay not isolated)* | expect slow first run after a gap; measure before scheduling |

### Not every mismatch is a bug

On 2026-10-15 the daily check showed about 70 landed vehicle files that bronze did not
list. The reflex was "ingest is dropping data". The investigation: they came in two
contiguous blocks at the same clock time on consecutive days, they were 15 bytes each, and
decoding one showed a valid feed with zero entities. The source publishes an empty feed
for ~11 minutes nightly, and an empty file yields no rows. Method worth repeating: find
exactly which items differ, look for a pattern (time, size, source), and inspect one by
hand before concluding.

### Changing a table's shape without breaking it

Adding a parsed field means changing the table. Doing that on the real table is the risky
way to find a bug. The habit used for decision 0009: (1) write the failing tests, (2) make
the change, (3) run the real entry point against a sandbox with old-schema empty tables and a
few real files, (4) check the result against an independent number from the same input, (5)
only then deploy. Schema evolution is explicit (`ALTER TABLE ... ADD COLUMNS` at ingest), not
silent (`mergeSchema`), so a surprise still fails loudly. See `docs/commands.md`, "Test a
bronze change in a sandbox". The same run also taught something the tests could not:
unset protobuf fields arrive as NULL, not as the spec's default, so NULL has to be read as the
default (here SCHEDULED).

### When your check and your pipeline disagree by one

On the first prod silver run the independent SQL count differed from the pipeline by a single
run in 11.8 million. Neither was exactly wrong: the SQL had no notion of a long silence, and the
pipeline's handling of one depended on how the work was batched. Chasing the one key (trip
87668894, silent for 86 minutes) exposed a design flaw: a result must not depend on batching.
The fix was to write the rule down (a run ends after an hour of silence), put it in the tested
reference, and make both sides apply it. Lessons: (1) do not dismiss a small mismatch; (2) an
independent check is only as good as its definition, so settle the definition first; (3) ask
whether a result would change if you reran in different-sized pieces.

Also seen in the prod rebuild: the first write after a fresh Auto Loader checkpoint can be
silent for 20 minutes or more, because the stream first lists every file in the landing folder
(about 25 minutes before the first table appeared for 8.5K vehicle files, against 11 minutes
for 4K earlier). That is consistent with listing time growing with file count; it is not a
hang. Raw volume small-file counts (about 12K files a day here) are therefore a real cost of
a rebuild, and a reason to keep the checkpoint when you can.

### Check what a field means before building on it

For stop arrivals the plan was to read `STOPPED_AT` plus `stop_id` as "the bus is at this stop". The
first real-data run showed estimates out of order and a 90-second disagreement with the geometry.
Looking at one trip fix by fix showed a bus sitting at the first stop for 25 minutes while its
`stop_id` named the *second*. The field is the next stop. Habits that caught it: (1) compare two
independent signals (position geometry against the feed's own stop pointer), (2) when they
disagree, print raw records for one trip before changing code, (3) measure rather than assume
(a unit check showed distances are in feet; 99.3 percent of stopped fixes sit at the previous
stop). A field name is a hypothesis about meaning; the data is the test.

### Check your check: a mismatch can be your own pull

A verification compared Spark's 15,576 rows with a Python reference that produced only 6,020. The
cause was in the check: the SQL API returns large results in pages, and the script read only the
first page (114,599 of 295,554 input rows), so the reference was starved. Before debugging the
pipeline, confirm the comparison's inputs are complete (compare the row count you received with
`manifest.total_row_count`). The helper now follows every page and asserts the count.

### Lessons that generalize

- **Look at the data, not the status.** Two of the bugs reported success.
- **Reproduce cheaply.** Use dev and a manual `bundle run` before touching prod.
- **Isolate environments.** Anything stateful (checkpoints, tables) must be namespaced per
  target.
- **Keep the irreplaceable thing simple.** The collector only appends files; all clever
  logic lives downstream where it can be re-run.

## 9. Dangerous operations and recoveries

| Situation | Do this | Notes |
|---|---|---|
| Stop ingest spending compute | set `ingest_pause_status: PAUSED` for the target in `databricks.yml`, then `bundle deploy -t <target>` | collection keeps running; files accumulate safely |
| Resume | set `UNPAUSED`, deploy | check `databricks jobs get <id>` shows `UNPAUSED` |
| Rebuild a bronze table | `DROP TABLE`, then delete that table's checkpoint folder, then run the job | both or neither; first run will be a full backfill |
| Bad deploy | fix, then deploy again; schedule uses whatever is deployed | there is no auto-rollback; revert the commit and redeploy |
| Token leaked or expired | create a new token, `wrangler secret put DATABRICKS_TOKEN`, revoke the old one | no redeploy needed |
| Runaway cost or quota hit | pause both jobs first, investigate second | |
| Free storage (raw files) | run `scripts/raw_retention_report.py`, read it, then delete only the folders it lists as deletable | irreversible; raw is the only copy of what KCM published; see decision 0005 |
| Stop collecting | `npx wrangler delete` (removes the Worker and cron) | you lose data for every minute it is off |

Never run a destructive command (drop, delete, `destroy`) without first listing what it
will remove. `bundle destroy` removes jobs and bundle files but not tables, checkpoints,
or landed files.

## 10. Cost and limits: how to think

- Free Edition publishes no compute or storage quota. The strategy is therefore
  **measure, record, and keep headroom**, not "assume it fits".
- Record numbers with a date in `docs/runbook.md` and `docs/limits.md`.
- Biggest levers: ingest frequency (30 min), rows kept in silver (trip updates are ~35K
  rows/snapshot), and raw retention (raw grows ~1.8 GB/day).
- Billing data comes from `system.billing.usage` and lags by hours, so yesterday's number
  is the freshest reliable one.

## 11. Doing it all from scratch, without an agent

A checklist of what exists and how to recreate it. Details are in `docs/commands.md`.

1. **Accounts:** Databricks Free Edition workspace; Cloudflare account (free plan);
   GitHub repo with CI (`.github/workflows/pr.yml`).
2. **Local tools:** `uv` (Python 3.12), Node 20+, Databricks CLI, `wrangler`.
3. **Authenticate:** `databricks auth login` creating profile `enpu`; verify with
   `databricks current-user me --profile enpu`.
4. **Create storage:** schemas `transit_landing`, `transit_ops` and volumes `raw`, `state`
   (SQL in `docs/commands.md`), or run `bundle run setup_job`.
5. **Token for the Worker:** create a personal access token in the workspace, store it with
   `npx wrangler secret put DATABRICKS_TOKEN` (and `DATABRICKS_HOST`).
6. **Deploy the collector:** `cd workers/collector && npm ci && npm run deploy`; confirm
   files land (`databricks fs ls ...`) *and that the cron fires* before moving anything.
7. **Deploy ingest:** local checks -> `bundle validate/deploy -t dev` -> `bundle run
   ingest_job -t dev` -> verify -> repeat for `-t prod`, then unpause.
8. **Operate:** the daily routine in section 7.

## 12. Glossary

| Term | Meaning |
|---|---|
| Asset Bundle | YAML + code that defines Databricks jobs; deployed with the CLI |
| Auto Loader | `cloudFiles` source that incrementally discovers new files |
| availableNow | trigger: process all pending data, then stop |
| Checkpoint | stream's stored progress; pairs with its target table |
| Delta | table format: Parquet files plus a transaction log |
| Medallion | bronze (raw typed) -> silver (clean, deduped) -> gold (metrics) |
| Micro-batch | one chunk of a streaming query, handled by `foreachBatch` |
| Serverless | Databricks-managed compute; Spark Connect |
| Unity Catalog | catalog/schema/table/volume naming and permissions |
| Volume | UC-managed file storage addressed by path |
| DBU | Databricks compute billing unit |

## 13. Where to read next

- Databricks docs: Asset Bundles, Auto Loader, Delta Lake, Jobs, system tables (search
  those names on docs.databricks.com).
- Cloudflare Workers docs: cron triggers, limits.
- In this repo: `design_doc.md` (scope and phases), `docs/decisions/` (why),
  `docs/status/` (where we are).
