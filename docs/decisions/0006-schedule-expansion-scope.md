# 0006: Versioned silver schedule tables; expand only service dates with realtime data

Date: 2026-10-15
Status: accepted (built and verified in dev; not deployed to prod)

## Context

KCM's static GTFS (`FAL26-161.2`, valid 2026-09-28 to 2027-03-26) has 32,635 trips and
1,126,734 stop times. A trip runs on many service dates, and the schedule changes at each
service change, so realtime trip ids must be matched against the schedule version valid for
their date. Expanding the whole feed window to one row per service date, trip, and stop
would be about 450K rows per day times about 170 days, roughly 76M rows, nearly all for
dates we never compare with realtime.

## Decision

- Load each landed feed zip into silver tables `gtfs_routes`, `gtfs_trips`, `gtfs_stops`,
  `gtfs_stop_times`, `gtfs_shapes`, `gtfs_calendar`, `gtfs_calendar_dates`. Every row carries
  `feed_version`; history is kept. A registry table `gtfs_feeds` records each loaded
  version (dates, source file, load time). A version already registered is skipped.
- `scheduled_stop_times` is expanded only for service dates between the first and last
  `start_date` seen in bronze vehicle positions, one date at a time, skipping dates already
  done. Each date uses the feed version valid that day (latest download if several).
- Times are stored as seconds since the GTFS day origin (`arrival_seconds`, may exceed
  86400) and converted with the day's origin epoch, which is "noon minus 12h" in
  `America/Los_Angeles`. The origin is computed once per date in Python
  (`day_origin_epoch`), so Spark does no timezone arithmetic.
- The work is one job, `silver_job`, with two tasks (load feeds, then expand),
  scheduled daily at 11:45 UTC, after the GitHub workflow's 11:17 download.

## Verification (dev, 2026-10-15)

Row counts equal an independent pure-Python computation from the zip: 142 routes, 32,635
trips, 1,126,734 stop times; scheduled rows 449,039 (12,938 trips) for 2026-10-13 and
449,085 (12,939 trips) for 2026-10-14. A stop at `29:30:00` on service date 2026-10-14 is
stored as 2026-10-15 12:30:00 UTC, 05:30 Pacific.

## Alternatives considered

- Expand the entire feed window up front: rejected (about 76M rows, mostly unused).
- Store `HH:MM:SS` strings and parse in queries: rejected; after-midnight and DST handling
  would be repeated in every query.
- Expand lazily at query time: rejected; the metrics joins in Phase 5 need it materialized.

## Consequences

- A new service change adds a new `feed_version`; dates after it use the new schedule
  automatically once the daily workflow uploads the zip and the job runs.
- If bronze ever has a `start_date` outside every loaded feed's window, that date is
  skipped with a message; the match-rate check in the realtime silver step will show it.
- Re-expanding a date replaces its rows, so a reload is safe.
