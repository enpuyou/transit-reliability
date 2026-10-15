# 0009: Bronze keeps the trip-level schedule_relationship; tables evolve by adding columns

Date: 2026-10-15
Status: accepted (verified in a sandbox; not yet deployed to prod)

## Context

Metro signals a canceled trip in the trip-updates feed with `trip.schedule_relationship =
CANCELED`. Our parser only kept the stop-level value, so in bronze a canceled trip looked like
an ordinary scheduled one: its stops carry predictions, so it would be counted as a trip with
real predictions in any prediction benchmark and as "announced but never seen" in service
delivery. Live snapshot 2026-10-15 21:55 UTC: 2 of 1,219 trips were `CANCELED`, both with
stop predictions still published.

## Decision

- Add `trip_schedule_relationship` (bigint) to bronze `vehicle_positions` and `trip_updates`.
  It is the GTFS-realtime `TripDescriptor.ScheduleRelationship` enum (0 SCHEDULED, 1 ADDED,
  2 UNSCHEDULED, 3 CANCELED, 5 REPLACEMENT, 6 DUPLICATED, 7 DELETED, 8 NEW) and is repeated on
  every row of the trip. The existing `schedule_relationship` stays the **stop-level** value.
- **NULL means not set, which the spec defines as SCHEDULED.** KCM sets the field only when it
  is not the default (36,107 of 36,177 sandbox rows were NULL). Silver must
  `coalesce(trip_schedule_relationship, 0)`. The JSON copies of the feeds show the default
  explicitly, which is misleading.
- A trip update with no stop updates still yields one trip-only row (stop columns NULL), so a
  trip-level fact is never lost for lack of stops. None occur in the feed today.
- Existing bronze tables gain the column through a deliberate step: before each ingest,
  `ensure_table_columns` compares the table to the contract and runs `ALTER TABLE ... ADD
  COLUMNS` for anything missing, and writes use the table's own column order. Old rows keep
  NULL. There is no `mergeSchema`, so an unexpected schema change still fails loudly.
- History is not rewritten. Files already ingested keep NULL until bronze is rebuilt from raw
  (drop table and checkpoint together). Do that when a canceled-trip history is needed.

## Verification (sandbox, 2026-10-15)

A throwaway prefix `evo_transit` with empty tables in the old schema and a three-file landing
volume holding a live snapshot. The new code added the column to both tables and wrote 36,177
trip-update rows (equal to the feed's stop updates), 70 flagged CANCELED: trip 801585571
(route 100249) 52 rows and trip 872393671 (route 100451) 18 rows, matching the JSON exactly.
A spot check showed no values shifted between columns. Vehicle positions: 584 rows.

## Consequences

- Canceled trips can be excluded from prediction benchmarks and counted separately in service
  delivery.
- In the cancellation case every stop is also marked SKIPPED at stop level: of 455 SKIPPED
  stops in the snapshot, 70 came from the two canceled trips. A stop-level SKIPPED count must
  exclude canceled trips to measure genuinely skipped stops.
- The next prod deploy will add the column on its first run and populate it going forward.
