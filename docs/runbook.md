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

Unit and meaning of the storage SKU (`DSU`) is not verified.

## Storage

| Date (UTC) | Item | Value |
|---|---|---|
| 2026-10-14 | `transit_bronze.trip_updates` | 146 MB, 36.2M rows (DESCRIBE DETAIL) |
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
