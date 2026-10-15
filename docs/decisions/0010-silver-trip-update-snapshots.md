# 0010: Silver trip-update snapshots as runs; match trips against a small trips-per-day table

Date: 2026-10-15
Status: accepted (built and verified in dev; not deployed to prod)

## Context

Bronze trip updates hold one row per stop prediction per snapshot, about 35K rows a minute,
and about two thirds repeat the previous snapshot (decision 0002). Silver also has to support
running more often than daily, which exposed a problem in the vehicle-position build:
`matched_trip` was computed against the stop-level schedule, which is only expanded once a day,
so rows arriving after local midnight but before that expansion would be stored with an unknown
match and never revisited (about 0.5 percent of a day's fixes, concentrated in the earliest
trips).

## Decision

**Trip-update snapshots.** One row is a *run*: a prediction for one stop of one trip on one
service date that stayed the same over consecutive snapshots, with `first_seen`, `last_seen`
and `snapshots`. A change in any of arrival time and delay, departure time and delay,
stop-level relationship, trip-level relationship, or `vehicle_id` starts a new run.
`trip_update_ts` is deliberately not compared (it moves on every update). Including
`vehicle_id` makes the moment a bus is assigned to a trip a run boundary.
- Trip-level relationship is `coalesce(..., 0)`: NULL means SCHEDULED (decision 0009). Older
  bronze tables without the column are read as 0.
- At most one row per (key, snapshot time) is used, so a feed version fetched twice counts once.
- The rules live in `silver_logic/runs.py` (pure, tested); the Spark build mirrors them.
- Incremental by event time. Each run re-reads the last hour (`LOOKBACK_SECONDS`) and
  processes three-hour windows, so a backfill never sorts everything at once. The open run of
  each key enters the fold as one pseudo-snapshot at its `last_seen`; snapshots at or before
  it are ignored, so the overlap is harmless. Results are written with a `MERGE` on
  `(trip_id, service_date, stop_sequence, first_seen)`: a continued run updates, a new run
  inserts.

**Matching.** A new table `scheduled_trips` (service_date, feed_version, trip_id, route_id,
direction_id, service_id; about 13K rows a day) is built from the trips table and the
service-day logic, for every realtime date through **tomorrow**, and dates from today on are
rebuilt each run (a newly loaded feed can change them). `matched_trip` uses it. A repair step
at the start of each vehicle-position build fills any NULL `matched_trip` once its date is
known. The principle: depend on the smallest table that answers the question; the 450K-row
stop-level table is only for metrics.

## Verification (dev, 2026-10-15)

- Service date 2026-10-14: silver runs 5,223,138 and sum of snapshots 17,406,350, equal to an
  independent plain-SQL computation from bronze (5,223,138 and 17,406,350). The table keeps 30
  percent of the rows.
- Staged run on a sandbox copy (route 100001, 2026-10-14, 121,888 bronze rows): folding up to
  the midpoint and then finishing gave 37,766 rows, identical to the single pass, with zero
  rows different in either direction, and 166 runs stitched across the boundary. A third run
  with nothing new changed nothing (37,766 rows, none duplicated).
- `scheduled_trips`: 12,938 trips on 2026-10-13, 12,939 on 2026-10-14, 12,938 on 10-15 and
  10-16. Vehicle positions: 1,220,692 rows, 0 unknown, 100 percent matched.
- Example: trip 562518701, stop 18: a flat prediction at the scheduled 07:55:17 is two runs,
  06:40:34 to 07:24:05 with no bus (45 snapshots) and 07:25:29 to 07:41:08 with bus 4353
  (17 snapshots). The bus was assigned about 15 minutes before departure.

## Alternatives considered

- Recompute runs from bronze for every affected trip each time: simpler, but re-reads a day of
  history per run.
- Expand the stop-level schedule a day ahead: about 450K rows a day for a flag that needs 13K.
- Compare only the predicted times: would hide when a bus is assigned.

## Consequences

- Silver can run every 30 minutes without unknown matches.
- A late-arriving snapshot older than the open run's `last_seen` is ignored, not inserted. Raw
  files cannot arrive that late in normal operation (ingest runs every 30 minutes); a rebuild
  from bronze recovers it.
- Predictions are not yet joined to the schedule or to observed arrivals; that is the next
  stage (stop visits).
