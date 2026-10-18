# 0015: Incident: silver vehicle positions silently kept only the last hour (2026-10-16 to 18)

Date: 2026-10-18
Status: fixed, repaired, and guarded

## What happened

The daily silver job ran unattended for the first time on 2026-10-16 at 11:45 UTC and reported
`SUCCESS` on 10-16, 17, and 18. In each run `silver.vehicle_positions` took only the bronze rows
ingested in the hour before the run and ignored the rest of the day. For service dates 2026-10-16 and
later, silver held about 5,000 fixes instead of about 1.4 million; 10-15 was partial. Everything built
on it was wrong for those dates: stop visits (arrival coverage 0.3 percent), trip performance (almost
every trip `not_observed`), prediction errors, and the two roll-ups. Quarantine kept receiving every
bad row, so its rate rose from 1.6 to 3.25 percent.

Not affected: bronze, the trip-update snapshots (checked against independent SQL for 10-17: 26,623,993
snapshots and 8,363,432 runs on both sides), and every figure in `docs/findings.md`, which used
2026-10-14 (built by the first full run). Found two days later by checking per-date coverage after
the scheduled runs; nothing alerted.

## Root cause

The build writes in two steps, rejected rows to quarantine and then good rows to silver. Both read
bronze through a filter "ingested after the latest row already processed", written as a scalar
subquery over the silver and quarantine tables. Spark evaluates that subquery lazily, when each write
runs. After the quarantine write had advanced the latest-processed time to "now", the silver write
evaluated the same filter and read only the last hour.

## Why the tests did not catch it

The first run (no tables yet) reads everything, so it works. My repeat-run test added no new bronze
data, so the filter had nothing to drop. The incremental path with *new data arriving between runs*
was never exercised for this table (it had been for trip-update snapshots, which compute their cursor
eagerly). The lesson is in the test, not the code: an idempotency test that reruns on identical data
proves little about an incremental build.

## Fix and verification

- The cutoff is computed once, eagerly, before any write (`_cutoff` in `silver_realtime.py`).
- An integrity check at the end of the build fails the task if any fix or quarantined row it meant to
  write is missing.
- Reproduced in a two-stage sandbox where new rows arrive between runs: 311,628 silver fixes against
  359,938 expected with the old code; exactly 359,938 and 6,312 quarantined with the fix.
- Repaired in prod without dropping anything: one backfill re-read bronze from 2026-10-13 (the MERGE
  inserts only what is missing) and rebuilt the downstream tables from 2026-10-15 using the new
  `--since` and `--rebuild-from` options (650 s). Afterwards silver equals an independent count of
  bronze up to the last ingested time (6,495,877 fixes and 107,708 quarantined on both sides), arrival
  coverage is 85 to 87 percent on each complete day, invariants hold, and the roll-ups conserve.

## Prevention

- A `health_checks` task now ends the daily job and fails it when: silver is more than 3 hours behind
  bronze; arrival coverage of a complete service date is below 50 percent; a scheduled trip has no
  trip-performance row; or a stop-visit invariant is violated. Logic is tested in
  `reliability/health.py`. A missing value fails.
- The staged incremental test (new data between runs, compared with an independent count) is now the
  standard check for incremental builds (`docs/commands.md`).
- Still open: a failure email. Without it a failed task only shows in the Jobs page; adding it needs
  the owner's address in the job configuration.
