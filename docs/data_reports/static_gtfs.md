# Static GTFS

| | |
|---|---|
| Status | **Observed** (profiled, not yet loaded) |
| Phase | 3 |
| Owner | King County Metro (`https://metro.kingcounty.gov/GTFS/google_transit.zip`, verified 2026-10-13, about 10.9 MB) |
| Format | ZIP of CSV files |
| Cadence | daily check, changes per service change |
| Profiled | 2026-10-14 |
| Target tables | silver schedule tables (routes, trips, stops, stop_times, shapes, calendar) |

## Executive summary

Static GTFS is the reference layer that gives meaning to realtime IDs. Realtime can say
“vehicle 4316 is at stop 2020 on route 100001.” Static GTFS can turn that into a route
name, stop name, scheduled time, direction, shape, and service calendar.

For a PM, static GTFS is what changes a moving-dot demo into a transit operations product.
It lets us compare what was planned with what happened. It also determines whether the
system covers buses only or includes additional modes such as rail, streetcar, or ferry.

## Core files

| File | One row means | What it unlocks |
|---|---|---|
| `agency.txt` | One transit agency | Agency identity and timezone |
| `routes.txt` | One route | Route names, colors, and `route_type` |
| `trips.txt` | One scheduled trip | Direction, headsign, service, and shape link |
| `stops.txt` | One stop or station | Stop names, coordinates, and hierarchy |
| `stop_times.txt` | One trip-stop schedule row | Scheduled arrivals and departures |
| `shapes.txt` | One point on a route shape | Route geometry and map rendering |
| `calendar.txt` | One recurring service pattern | Weekday/date service |
| `calendar_dates.txt` | One service exception | Holidays and special service |
| `frequencies.txt` | One headway rule | Frequency-based service |
| `feed_info.txt` | Feed metadata | Version and validity dates |

## Example records

Representative examples, using the fields that will be joined to KCM realtime:

```text
routes.txt
route_id,route_short_name,route_long_name,route_type
100001,<route name>,<route destination>,3

stops.txt
stop_id,stop_name,stop_lat,stop_lon
2020,<stop name>,47.6452,-122.3702

trips.txt
route_id,service_id,trip_id,trip_headsign,direction_id,shape_id
100001,<service>,562519421,<headsign>,1,<shape>

stop_times.txt
trip_id,arrival_time,departure_time,stop_id,stop_sequence
562519421,08:10:00,08:10:30,2020,2
```

These are structural examples, not a claim about the final downloaded feed values. The
actual static feed must be downloaded and profiled before this report is marked Observed.

## What this means

The realtime `route_id`, `trip_id`, and `stop_id` are foreign keys into this reference
system. The join is the central relationship in the project:

```text
realtime vehicle position
  -> trip_id + route_id + stop_id
  -> static trip + route + stop + stop_times + shape
  -> live map, schedule comparison, and reliability metrics
```

GTFS times can exceed `24:00:00`. For example, `25:10:00` means 1:10 AM on the next
calendar day but still belongs to the previous service day. The loader must preserve this
meaning in `America/Los_Angeles`, including daylight-saving transitions.

## What we can do with this source

- Human-readable route and stop maps.
- Scheduled transit time between stops.
- Planned trip timelines.
- Route geometry and stop placement.
- Mode classification through `route_type`.
- Schedule-versus-realtime delay.
- Segment runtime and schedule padding analysis.
- Service delivery analysis against the planned trip list.
- Detection of schedule changes by feed version.

## Important distinction: schedule time versus observed time

Static GTFS tells us what should happen. It does not tell us whether the bus actually
arrived. Observed arrival must come from historical vehicle positions, stop status, or a
validated inference method. Agency trip updates provide predictions, which are useful but
are not the same as observations.

## Engineering plan and quality checks

1. Download and retain each feed version with its download date and file hash.
2. Parse the core CSV files into versioned tables.
3. Expand calendar rules into active service dates.
4. Convert GTFS service times into timezone-aware timestamps.
5. Join realtime trip IDs against the feed version active on each service date.
6. Quarantine unmatched trips and malformed rows instead of silently dropping them.
7. Test a known trip, a route shape, and an after-midnight service time.

## Product story

Static GTFS answers the planner's baseline question: **what was the agency trying to
operate?** With it, a map can show more than current dots. It can show the planned route,
which stops should occur next, how much time was scheduled between stops, and where actual
performance diverged from the plan.

The report should be updated with real route names, stop names, mode counts, feed version,
row counts, and a sample route map once the source is downloaded.

## Observed behavior (profiled 2026-10-14)

Downloaded `google_transit.zip` (10,862,777 bytes; `Last-Modified` 2026-09-28 11:55 UTC).

| Item | Value |
|---|---|
| Feed version / validity | `FAL26-161.2`, 2026-09-28 to 2027-03-26 |
| Agencies in the feed | 4: Metro Transit (1), City of Seattle streetcar (23), Sound Transit (40), Solid Ground EZ Loop (6289). All `America/Los_Angeles`. |
| Routes / trips / service ids | 142 routes, 32,635 trips, 15 service ids in trips |
| `stop_times.txt` | 1,126,734 rows (66 MB uncompressed), the largest table |
| `shapes.txt` | 7.6 MB uncompressed |
| After-midnight times | 39,424 stop times (3.5 percent) have an arrival hour of 24 or more; the maximum hour is 29 |
| Service definition | `calendar.txt` has only 20 weekly patterns (many with every weekday off); `calendar_dates.txt` has 1,081 exceptions (620 added, 461 removed). Most real service is defined by exceptions. |

**Match against our realtime data** (bronze vehicle positions, 2026-10-14):

| Check | Result |
|---|---|
| Realtime `(trip_id, route_id)` pairs found in this feed | 12,556 of 12,556 trips; every route id also found (100 percent, 1.84M rows) |
| Trips seen in realtime on a date that the schedule says run that date | 6,689 of 6,689 on 2026-10-13 and 8,749 of 8,749 on 2026-10-14 (100 percent) |
| Scheduled trips seen in realtime | 51.7 percent on 10-13 (collection began mid-afternoon), 67.6 percent on 10-14 (day not finished); not a quality problem |

Caveat: this is a single feed version. Collection began after 2026-09-28, so no version
mismatch has been exercised yet; the first one will occur at the next service change, which
is why the schedule tables are versioned by feed version and date.

## Related reports

- [Data report index](README.md)
- [KCM realtime](kcm_realtime.md)
