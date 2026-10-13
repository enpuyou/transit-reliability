# 0002: Keep raw complete, reduce in silver

Date: 2026-10-13
Status: accepted

## Context

Measured on five consecutive pulls (2026-10-13):

- Vehicle positions: about 98 percent of vehicles have a new position time each pull;
  10 to 13 percent report a new time without moving.
- Trip updates: about 67 percent of stop predictions are identical to the previous pull;
  about 33 percent change. A snapshot is about 35,000 rows (about 50 million rows per
  day). Predictions reach up to about 3 hours ahead.
- Alerts: nearly all alerts repeat between pulls.
- Raw volume is about 1.8 GB per day. Databricks Free Edition publishes no storage or
  compute numbers (see `docs/limits.md`).

## Decision

1. Raw files and bronze tables keep every pull. The collector and bronze never filter,
   because the feeds are not recoverable and the unchanged prediction is itself data for
   the Phase 7 agency baseline.
2. Silver reduces volume:
   - trip updates: one row per change of `(trip_id, start_date, stop_sequence)`, with
     `first_seen` and `last_seen` feed times;
   - alerts: one row per alert version with first and last seen;
   - vehicle positions: keep all rows, deduped on `(vehicle_id, vehicle_ts)`.
3. Consider limiting stored trip-update horizon to the next 60 minutes of stops per trip
   (the Phase 7 target is 5 to 30 minutes ahead). Decide in Phase 3 once the planner
   views that need full-trip predictions are known.
4. Delete raw trip-update files older than 30 days after silver is verified, to bound
   storage.
5. Do not use `entity_id` as a key; it changes every pull. About five vehicle IDs per
   snapshot appear twice and must be deduped.

## Consequences

- Bronze trip updates are the largest table; its growth must be tracked in the runbook.
- Silver logic is more complex than append-only, but cuts rows roughly threefold or more.
- If storage or compute limits force it earlier, apply point 3 and 4 first.
