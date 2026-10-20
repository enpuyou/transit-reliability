# Runbook: measured baselines

Dated measurements of what the pipeline costs and how it behaves. Add a row each time you
measure; do not overwrite old rows. Procedures live in `docs/commands.md`.

## Ingest job (prod, serverless)

| Date (UTC) | Run | Duration | Notes |
|---|---|---|---|
| 2026-10-14 | first run, shared checkpoint (bug) | 83 s | only 10 files ingested; invalid |
| 2026-10-14 | backfill with own checkpoint | 1,515 s | ~1.5 days of files; ~11 min before first table write |
| 2026-10-14 22:00 | scheduled | 112 s | 30 minutes of new files |

## Ingest run durations (prod, scheduled)

| Period (UTC) | Runs | Duration (s) | Notes |
|---|---|---|---|
| 2026-10-14 22:00 to 2026-10-15 19:00 | 43, all SUCCESS | min 112, median 176, max 264 | the first scheduled run was 112 s; runs lengthen as tables grow; watch this |

## Static refresh job (dev)

| Date (UTC) | Run | Duration | Notes |
|---|---|---|---|
| 2026-10-15 | first run | 198 s | load_feeds 146 s (1.13M stop times, 167K shape points), expand_schedule 44 s for 2 dates |

| 2026-10-15 | silver_job first run (3 tasks) | 136 s | load_feeds skipped (already loaded), expand skipped, vehicle_positions 39 s for 1.46M bronze rows |

| 2026-10-15 | silver_job, 5 tasks, first full run | 204 s | trip_updates 200 s for 34M bronze rows (8 three-hour chunks); expand_trips 52 s; vehicle_positions 44 s; load_feeds 32 s; expand_schedule 28 s |

| 2026-10-15 22:43 | prod bronze rebuild from raw (3 sources, ~3 days of files) | 2,903 s (48 min) | first table appeared after about 25 minutes; vehicle positions (8.5K files) finished about 31 min in, trip updates (3K files) about 9 min later |
| 2026-10-15 23:32 | prod catch-up ingest after the rebuild | 96 s | |
| 2026-10-15 23:35 | prod silver_job, first run (5 tasks) | 358 s | trip_updates 355 s for 76M bronze rows; load_feeds 98 s |
| 2026-10-16 00:17 | prod trip-update snapshots rebuilt (silence rule) | 1,116 s | |

| 2026-10-16 01:36 | dev stop_visits, 2 service dates | about 4 min | 449K rows each; output equals the pure code on all 6,896 stop visits of 200 trips |
| 2026-10-16 01:41 | prod stop_visits, 3 service dates | 319 s | |

| 2026-10-16 | prod trip_performance, 3 service dates | 57 s | 294-trip sandbox build took 41 s; output equals a Python reference on 6,174 values |

| 2026-10-16 | prod prediction_errors, 3 service dates | 79 s | 300-trip sandbox build 84 s; output equals the Python reference on 15,576 rows |

| 2026-10-16 | prod route_performance and prediction_accuracy (both), 3 service dates | 57 s | conservation checks equal to the detail tables |

| 2026-10-16 11:45 | silver_job, first unattended run | 595 s | reported SUCCESS but silver vehicle positions kept only the last hour (decision 0015) |
| 2026-10-17 11:45 | silver_job, scheduled | 458 s | same fault |
| 2026-10-18 11:45 | silver_job, scheduled | 421 s | same fault |
| 2026-10-18 21:00 | repair: vehicle positions backfill plus downstream rebuild from 10-15 (one submitted run) | 650 s | silver now equals an independent count of bronze |

Observed 2026-10-16 to 18: bronze ingest 96 of 96 runs succeeded in 48 hours (113 to 204 s, median 174 s,
no gaps). The scheduled GTFS download ran 3 to 4 hours after its 11:17 UTC schedule (14:13, 14:38, 15:23);
harmless with a daily silver build (the new schedule is loaded at the next run, and trips are matched at
build time), but a reason not to assume the download precedes the 11:45 silver run.

## Compute (system.billing.usage)

Query: see "Databricks compute and storage" in `docs/commands.md`. The table lags by hours.

| Date (UTC) | SKU | Quantity | Notes |
|---|---|---|---|
| 2026-10-13 | jobs serverless compute | 0.457 DBU | dev setup and ingest attempts |
| 2026-10-13 | serverless SQL compute | 2.226 DBU | manual checking queries, not the job |
| 2026-10-13 | all-purpose serverless | 0.111 DBU | |
| 2026-10-14 | jobs serverless compute | 5.806 DBU | prod backfill and the first scheduled runs (from 20:30) |
| 2026-10-14 | serverless SQL compute | 6.354 DBU | manual queries (checking, profiling); not the pipeline |
| 2026-10-14 | all-purpose serverless | 0.114 DBU | |
| 2026-10-15 (to ~19:30) | jobs serverless compute | 12.567 DBU | ~38 scheduled runs, about 0.33 DBU per run. Extrapolated to a full day: about 15 DBU (an estimate; runs are lengthening) |
| 2026-10-15 (to ~19:30) | serverless SQL compute | 0.785 DBU | manual queries |
| 2026-10-15 (full day) | jobs serverless compute | 21.52 DBU | 48 scheduled ingests, the 48 minute bronze rebuild, the first prod silver run, dev silver runs, sandbox tests |
| 2026-10-15 (full day) | serverless SQL compute | 10.74 DBU | **my verification queries** (window functions over tens of millions of rows); more than the pipeline's own SQL use, so heavy verification has a real cost |
| 2026-10-15 (full day) | all-purpose serverless | 0.16 DBU | |

Unit and meaning of the storage SKU (`DSU`) is not verified.

## Storage

| Date (UTC) | Item | Value |
|---|---|---|
| 2026-10-14 | `transit_bronze.trip_updates` | 146 MB, 36.2M rows (DESCRIBE DETAIL) |
| 2026-10-16 00:50 | `transit_bronze.trip_updates` | 313 MB, 76.1M rows |
| 2026-10-16 00:50 | `transit_silver.trip_update_snapshots` | 348 MB, 25.6M rows: fewer rows but **more bytes** than bronze (compression already handled repeats) |
| 2026-10-16 00:50 | `transit_silver.vehicle_positions` | 33 MB, 2.97M rows |
| 2026-10-15 19:30 | `transit_bronze.trip_updates` | 262 MB, 67.1M rows |
| 2026-10-15 19:30 | `transit_bronze.vehicle_positions` | 31 MB, 3.03M rows |
| 2026-10-15 19:30 | `transit_bronze.alerts` | 0.27 MB, 44K rows |
| 2026-10-15 19:46 | raw files landed | 2.659 GB over about 46 hours of collection: trip updates 2.321 GB (2,774 files), vehicle positions 0.301 GB (8,048), alerts 0.026 GB (555), static GTFS 0.011 GB. About 1.4 GB/day measured, below the 1.8 GB/day estimate because overnight files are small |
| 2026-10-15 19:30 | bronze tables total | about 0.29 GB |
| 2026-10-14 | raw files | about 1.8 GB/day (computed from file sizes, not measured on the volume) |

## Static GTFS workflow runs

| Date (UTC) | Result | Notes |
|---|---|---|
| 2026-10-15 | first run failed at upload | `fs cp` does not create the dated folder; fixed with `fs mkdir` |
| 2026-10-15 | success, uploaded | 10,862,777 bytes, SHA-256 `d04d6b52...ad74e`, identical to the locally profiled copy |
| 2026-10-15 | success, skipped | second run correctly logged "Feed unchanged" |

## Credentials and expiry

| Credential | Used by | Created | Expires | Notes |
|---|---|---|---|---|
| `DATABRICKS_TOKEN` (GitHub secret) | daily GTFS workflow | 2026-10-15 (secret timestamp 19:21 UTC) | unverified: the setup steps said 90 days (about 2027-01-13), but the lifetime you chose was not checked | dedicated token; confirm the real expiry in the Databricks UI and correct this row |

## Worker live tail (wrangler tail)

| Window (UTC) | Runs | Outcome | CPU (ms) | Start delay vs scheduled | Notes |
|---|---|---|---|---|---|
| 2026-10-15 19:56 to 20:05 | 9 | 9 ok, 0 exceptions, every upload ok | 9 to 16 (median 11; 16 on the alerts minute) | 3 to 6 s; 19 s at 20:00 | spans the top-of-hour boundary; hour-boundary "gaps" in landed files are late runs, not lost ones |

## Failure email

The prod ingest and silver jobs email the deploying user when a run fails
(`email_notifications.on_failure` in `resources/*.yml`). Check it is set:
`databricks jobs get <job_id> --profile enpu -o json` and look at `settings.email_notifications`.
Delivery has not been tested with a real failure (unverified); a failed health check fails the
silver job, so it also sends mail.

## Monitoring (since 2026-10-19; decision 0017)

| Layer | Where | Cadence | Reaches you by |
|---|---|---|---|
| Hourly checks | job `transit-reliability-prod-monitor` | hourly at :12 | Databricks email on failure |
| Daily checks | last task `daily_checks` of the silver job | daily after 11:45 UTC | Databricks email on failure |
| Results history | `workspace.transit_ops.check_results` | every run | SQL editor, proof report |
| External watchdog | GitHub workflow `watchdog` | hourly at :20 | GitHub email on failure |

If the watchdog says "could not check", suspect the Databricks token (rotate: `docs/commands.md`,
"Token rotation"; the GitHub secret `DATABRICKS_TOKEN` is separate from the Worker's). GitHub disables
scheduled workflows after 60 days without repository activity (unverified for this repository): push
or run any workflow by hand to keep it alive. Compute cost of the monitor job: not yet measured.


## Dispatcher token and GitHub schedule (2026-10-20; decision 0020)

The Worker `transit-dispatcher` triggers the site/probe workflow every 15 minutes and the watchdog hourly
using a fine-grained GitHub token (secret `GITHUB_TOKEN` in Cloudflare; Actions read and write on this
repository only). Token expiry date: not yet set (record it here when created). When the token is missing
or expired the probes stop and the public page turns red with "Cannot confirm the pipeline is alive".
GitHub's own schedule is only a fallback. Databricks CLI login (`databricks auth login --profile enpu`)
also expires; on 2026-10-20 both CLI profiles were invalid ("Refresh token is invalid") and had to be
renewed in a browser; scheduled jobs and the GitHub workflows (which use their own token) were unaffected.
