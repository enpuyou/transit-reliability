# Runbook: measured baselines

Dated measurements of what the pipeline costs and how it behaves. Add a row each time you
measure; do not overwrite old rows. Procedures live in `docs/commands.md`.

## Ingest job (prod, serverless)

| Date (UTC) | Run | Duration | Notes |
|---|---|---|---|
| 2026-10-14 | first run, shared checkpoint (bug) | 83 s | only 10 files ingested; invalid |
| 2026-10-14 | backfill with own checkpoint | 1,515 s | ~1.5 days of files; ~11 min before first table write |
| 2026-10-14 22:00 | scheduled | 112 s | 30 minutes of new files |

## Compute (system.billing.usage)

Query: see "Databricks compute and storage" in `docs/commands.md`. The table lags by hours.

| Date (UTC) | SKU | Quantity | Notes |
|---|---|---|---|
| 2026-10-13 | jobs serverless compute | 0.457 DBU | dev setup and ingest attempts |
| 2026-10-13 | serverless SQL compute | 2.226 DBU | manual checking queries, not the job |
| 2026-10-13 | all-purpose serverless | 0.111 DBU | |
| 2026-10-14 | jobs serverless compute | not yet reported | unverified; recheck 2026-10-15 |

Unit and meaning of the storage SKU (`DSU`) is not verified.

## Storage

| Date (UTC) | Item | Value |
|---|---|---|
| 2026-10-14 | `transit_bronze.trip_updates` | 146 MB, 36.2M rows (DESCRIBE DETAIL) |
| 2026-10-14 | raw files | about 1.8 GB/day (computed from file sizes, not measured on the volume) |

## Credentials and expiry

| Credential | Used by | Created | Expires | Notes |
|---|---|---|---|---|
| `DATABRICKS_TOKEN` (GitHub secret) | daily GTFS workflow | not created yet | 90 days after creation | dedicated token; record the date here when created |
