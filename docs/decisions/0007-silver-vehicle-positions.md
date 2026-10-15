# 0007: Silver vehicle positions: dedupe on the GPS fix, quarantine with reasons, flag schedule matches

Date: 2026-10-15
Status: accepted (built and verified in dev; not deployed to prod)

## Context

Bronze keeps every snapshot, so the same GPS fix appears in several snapshots and the feed
contains placeholder rows. Measured on prod bronze 2026-10-15 (3.17M rows): 42,227 rows with
no `vehicle_ts`, 49,551 with a zero coordinate, 22 more than an hour stale, none outside a
King County box, none missing a trip, route, or vehicle id, and every route present in the
schedule. 2.70M distinct `(vehicle_id, vehicle_ts)` among 3.17M rows.

## Decision

- Grain: one row per distinct fix, keyed `(vehicle_id, vehicle_ts)`; earliest snapshot wins.
- Quality rules (first match wins, one reason per row), defined once in
  `quality/rules.py` with a Spark expression built from the same constants:
  `NO_VEHICLE_TS`, `ZERO_COORDINATES`, `OUT_OF_BOUNDS`, `TS_IN_FUTURE` (over 60 s ahead of
  the feed), `TS_STALE` (over 1 hour behind), `MISSING_VEHICLE_ID`, `MISSING_TRIP_ID`.
  Failing rows go to `quarantine_vehicle_positions` and are never silently dropped.
- A trip not in the schedule is **kept** with `matched_trip = false`, not quarantined, so
  the match-rate metric stays honest. `matched_trip` is NULL (unknown) when the service
  date has not been expanded yet.
- Manual smoke files are excluded.
- Incremental by bronze `ingested_at` watermark with a 1 hour lookback; writes use
  `MERGE ... WHEN NOT MATCHED INSERT` on the key (quarantine keys on a row hash), so overlap
  and reruns are idempotent. The watermark is read from the silver and quarantine tables
  themselves, so there is no separate state to lose; rebuild by dropping both together.
- Runs as the third task of the daily `silver_job`, after the schedule is expanded.

## Verification (dev, 2026-10-15)

Bronze 1,455,844 non-smoke rows. Independent plain-SQL expectation vs the job: quarantine
23,350 and 23,350; silver fixes 1,220,692 and 1,220,692. Quarantine rate 1.60 percent.
Match rate 100 percent on 2026-10-13 and 2026-10-14, no unknown rows. A second run left
the counts unchanged. Reasons: NO_VEHICLE_TS 19,818, ZERO_COORDINATES 3,532.

## Consequences

- Rules exist in two forms (Python reference and Spark expression). The Python tests pin the
  rules; the Spark side is checked by comparing counts with an independent SQL query.
  Change both together.
- Silver lags bronze by up to a day because the job is daily. Phase 4's live views will need
  a more frequent run or a different source; decide then.
- Trip updates and alerts silver tables are still to build.
