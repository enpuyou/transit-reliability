# Project Status: Phase 2 Progress

Date: 2026-10-13
Branch: `main`
Remote: `origin/main`
Latest pushed commit: `5bc4773` (`feat: parse GTFS realtime feeds`)

## Executive summary

The project has completed the documentation and local GTFS-Realtime parser milestones and
is partway through Phase 2, Bronze ingestion.

The local parser and bronze schema contract pass all local checks. The Databricks bundle
validates and deploys successfully. The setup job also succeeds in the dev workspace.
The end-to-end bronze ingestion job has been attempted with checked-in fixtures, but the
job currently fails during runtime. The failure has not yet been reduced to a final
exception because the Databricks task output was large and the follow-up traceback query
was cancelled. No Phase 2 implementation changes have been committed or pushed yet.

## Repository and Git state

### Pushed commits

1. `720eba0` - initial repository setup
2. `17a8bbb` - data reports and command reference
3. `5bc4773` - GTFS-Realtime parsers and parser tests

### Current local changes

The following Phase 2 work is present locally and uncommitted:

- `databricks.yml`
- `resources/setup_job.yml`
- `resources/ingest_job.yml`
- `sql/setup.sql`
- `src/transit_reliability/bronze/`
- `src/transit_reliability/entrypoints/`
- `tests/test_bronze_schema.py`
- Updates to `pyproject.toml`
- User/local edits in the existing GTFS-Realtime parser files and parser tests

The branch currently tracks `origin/main`; the Phase 2 changes are not on the remote.
`.env` remains ignored and was not added.

## Completed work

### Documentation and planning

- Added the command reference in `docs/commands.md`.
- Added the overall data inventory in `docs/data_inventory.md`.
- Added the source-by-source data catalog in `docs/data_reports/`.
- Recorded Databricks schema, volume, and Files API smoke-test results in
  `docs/decisions/0000-setup-notes.md`.
- Confirmed the inbound Files API path works for the landing volume.

### GTFS-Realtime parsing

- Added pure parsers for:
  - vehicle positions;
  - trip updates, exploded to one row per stop-time update;
  - service alerts.
- Parsers preserve feed headers, entity IDs, identifiers, timestamps, optional values,
  alert text, active periods, and informed entities.
- Existing fixtures are used as parser inputs.

### Phase 2 local contracts

- Added bronze column contracts for vehicle positions, trip updates, and alerts.
- Added source-to-table name validation.
- Added Databricks entrypoints:
  - `setup_schemas`
  - `ingest_bronze`
- Added Auto Loader configuration using `binaryFile`, `availableNow`, and per-source
  checkpoint paths.
- Added dev and prod bundle targets with schema-prefix variables.
- Development ingest is deliberately paused; production is configured as unpaused.
- Added mypy configuration for Databricks-only Spark and Pandas runtime imports.

## Validation results

The following local checks pass as of this status update:

```text
7 tests passed
Ruff: all checks passed
Mypy: no issues found in 11 source files
```

The local suite includes:

- fixture protobuf decoding;
- vehicle-position parser behavior;
- trip-update stop expansion;
- alert parsing;
- bronze schema metadata requirements;
- supported-source and table-name validation.

## Databricks verification

### Confirmed

- Bundle validation succeeds for `dev`.
- Bundle deployment succeeds for `dev`.
- Dev setup job succeeds after changing serverless environment configuration to
  `environment_version: "3"`.
- Dev jobs exist and are paused as intended:
  - `[dev youenpuyou] transit-reliability-dev-setup`;
  - `[dev youenpuyou] transit-reliability-dev-ingest`.
- Both jobs use serverless environment version 3.
- The three checked-in fixture files were uploaded to the shared landing volume under
  the current UTC date:
  - `raw/vehicle_positions/dt=2026-10-13/vehiclepositions-smoke.pb`
  - `raw/trip_updates/dt=2026-10-13/tripupdates-smoke.pb`
  - `raw/alerts/dt=2026-10-13/alerts-smoke.pb`

### Not yet complete

The bronze ingest job was run twice against the smoke inputs and failed both times with a
Databricks task-level `Workload failed` error. The precise child-task traceback still needs
to be extracted from the Databricks run output.

The current implementation was changed from direct streaming `mapInPandas` to a
`foreachBatch` pattern after the first attempt, but the retry still failed. Therefore,
the runtime failure may be in Spark schema conversion, the parser closure, Delta table
creation, or another Databricks-specific execution detail. It should not be marked solved
until the child task traceback is captured.

No bronze table row counts have been verified yet.

## Current Phase 2 exit status

| Requirement | Status |
|---|---|
| Pure protobuf parsers | Complete locally |
| Parser unit tests | Complete |
| Bundle configuration | Validates and deploys |
| Dev/prod target structure | Complete locally |
| Setup schemas/state volume job | Verified successful in dev |
| Raw fixture inputs in landing volume | Verified |
| Auto Loader bronze job | Failing; needs diagnosis |
| Bronze table row counts | Not verified |
| Idempotent rerun check | Not verified |
| Compute usage baseline | Not recorded |
| 24-hour scheduled ingest | Not started |
| Phase 2 commit and push | Not done |

## Next actions

1. Retrieve the child task run output for the latest ingest run and capture the final
   traceback in the next decision/status note.
2. Fix the smallest Databricks runtime issue identified by that traceback.
3. Rerun the dev ingest job against the three smoke files.
4. Verify the three bronze tables and record expected row counts:
   - vehicle positions: 522 rows;
   - trip updates: 33,772 rows;
   - alerts: 81 rows.
5. Rerun the job and confirm checkpoints prevent duplicate file processing.
6. Record job duration and compute usage.
7. Review the diff, commit the completed Phase 2 slice, and push it to `origin/main`.
8. Only then begin the 24-hour scheduled-ingest exit check.

## Important interpretation

The project is not yet in Phase 3. Static GTFS and silver modeling should wait until the
bronze job can ingest and rerun idempotently. The current blocker is operational execution
on Databricks, not the local parser design.

## Update: bronze ingest fixed and verified (2026-10-13)

Root cause: `streaming.py` built `/Volumes/{volume}` from dotted names
(`workspace.transit_landing.raw`) without converting dots to slashes. The `foreachBatch`
version also used `rdd`/`createDataFrame(rdd)`, which serverless does not support, and
optional ints would have become NaN floats in pandas. Fixes: `volume_path()` helper,
`mapInPandas` inside `foreachBatch` with nullable `Int64`, Spark-stamped `ingested_at`, and
`txnAppId`/`txnVersion` on the Delta write.

| Requirement | Status |
|---|---|
| Auto Loader bronze job | Succeeds in dev (about 114 s first run) |
| Bronze row counts | 522 / 33,772 / 81, as expected |
| Idempotent rerun | Verified: counts unchanged, run about 71 s |
| Compute usage baseline | Durations only; DBU usage not yet recorded |
| 24-hour scheduled ingest | Not started |

Local checks: 9 tests pass, ruff and mypy clean.

Open items: `sql/setup.sql` duplicates `setup_schemas` and is unused; prod target likely
needs `root_path`/permissions before deploy.
