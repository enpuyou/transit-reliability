# Project Status: Phase 2 wrap-up

Date: 2026-10-14
Branch: `main` (commits `71f52ec`, `9c60fc6` plus the docs change that follows them; not pushed)

## Summary

Phase 1 (collector) has run since 2026-10-13 21:43 UTC. Phase 2 (bronze) is complete in
function: prod ingest runs every 30 minutes into `transit_bronze.*`. The formal exit
criterion (24 hours of scheduled prod runs, then a compute reading) is in progress; the
clock started 2026-10-14 ~22:00 UTC.

## Phase 2 exit status

| Requirement | Status |
|---|---|
| Pure parsers and tests | Complete (9 tests) |
| Bundle dev and prod targets | Complete; prod `root_path` set |
| Bronze row counts verified | Done: prod backfill matched landed files (see below) |
| Idempotent rerun / no duplicate file processing | Verified: 0 files with more than one `ingested_at` |
| Prod ingest on schedule | Running since 2026-10-14 22:00; first scheduled run took 112 s |
| 24 hours of scheduled runs | Pending (check 2026-10-15 ~22:00) |
| Compute usage measured | Partial: 2026-10-13 only; 2026-10-14 not yet reported (`docs/runbook.md`) |

Backfill verification (2026-10-14 ~21:51): vehicle positions 4,050 of 4,129 landed files,
trip updates 1,443 of 1,455, alerts 291 of 291. The remainder landed during the run and
is picked up by scheduled runs.

## Incidents this phase (details in `docs/decisions/0004-prod-ingest-and-checkpoints.md`)

- Out-of-memory on the trip-update backlog: fixed with batch caps.
- Dev and prod shared a checkpoint: prod finished "successfully" with 10 files. Fixed with
  per-prefix checkpoints; prod tables rebuilt from raw.

## Collection health (2026-10-14, from landed files)

Gaps over thresholds: trip updates 4 (about 8 runs lost; largest 368 s at 02:45, which
also hit vehicle positions), vehicle positions 3 (about 19 fetches lost). Causes not
investigated. One Worker run measured 12 ms CPU against the 10 ms free-plan limit and still
reported `ok` (single sample, unverified as typical).

## Open items

- Verify the 24-hour exit criterion on 2026-10-15 and record DBU in `docs/runbook.md`.
- Explain the ~11 minute delay before the first table write in the backfill run.
- Longer `wrangler tail` CPU sample; investigate the 02:45 outage.
- Dev tables are stale; drop `dev_transit_*` when no longer needed. `sql/setup.sql` is an
  unused duplicate of `setup_schemas`; `ops.job_runs` from the design doc is not built.
- Commits are local; nothing pushed.

## Deferred to the next deploy pass (do these together, after Phase 3 or 4 work)

Not urgent; batched so prod is deployed and tested once. Nothing here changes data we need.

1. **Deploy the trimmed bundle sync to prod.** Already verified in dev (39 files removed, setup
   job `SUCCESS`). Run `bundle validate -t prod` first, then `bundle deploy -t prod`, then
   confirm the next scheduled prod ingest is `SUCCESS` and bronze file counts still match
   what landed.
2. **Drop the stale dev bronze tables:** `workspace.dev_transit_bronze.vehicle_positions`,
   `.trip_updates`, `.alerts`. They stopped at 2026-10-14 20:30 and are missing 13 files.
   Do not run the dev ingest job before dropping them: its checkpoint is new, so it would
   re-read every landed file into these tables and create duplicates.
3. **Delete the orphaned old checkpoints** under
   `/Volumes/workspace/transit_ops/state/checkpoints/`: `bronze_alerts`,
   `bronze_trip_updates`, `bronze_vehicle_positions` (the pre-fix shared names). Nothing
   uses them now: prod uses `transit_bronze_<source>`, dev would use
   `dev_transit_bronze_<source>`. List the folder first and confirm only those three
   remain beside the prefixed ones.
4. Remove the unused `sql/setup.sql` (duplicate of `setup_schemas`).

Destructive steps (2 and 3) need a listing of exactly what will go before running them.

## Next: Phase 3 (static GTFS and silver)

1. Daily GTFS zip download and versioned load of schedule tables, including service-date
   expansion and times past 24:00.
2. Silver tables: dedupe per the CLAUDE.md key rules, drop vehicle placeholder rows,
   quarantine table with reasons. Trip updates need a volume-reduction design (36M rows in
   about 1.5 days).
3. Targets: trip match rate above 95%, quarantine rate under about 2%.
