# The silver layer: what each table is, why it is built this way, and what it is for

Read this to understand the *purpose* of silver, not just its columns. Numbers marked
*(dev, 2026-10-15)* were measured on the dev copy built from bronze up to 2026-10-14 20:30 UTC.

## 1. Why a silver layer exists at all

| Layer | Question it answers | Shape | Who can trust it |
|---|---|---|---|
| Raw files | What exactly did KCM publish at that moment? | one binary file per snapshot | nobody reads these; they are the insurance policy |
| Bronze | What was in each snapshot? | one row per vehicle (or prediction, or alert) **per snapshot**, nothing removed | engineers debugging; not analysts |
| **Silver** | What happened in the world, once, cleanly, tied to the schedule? | one row per real-world event, deduplicated, validated, joined to the plan | analysts and the metrics code |
| Gold | What do planners need to see? | metrics per route, hour, stop | planners, the website |

Bronze is deliberately faithful and messy: every snapshot is kept, so the *same* GPS fix
appears in many snapshots, and the feed contains placeholder rows. If every query had to
undo that, every query would repeat the same cleanup differently and get different answers.
Silver does the cleanup **once, in one place, with the rules written down**, so everything
above it can rely on a simple promise: *one row means one real thing, and bad rows are
somewhere else with a reason attached.*

The three jobs of silver:

1. **Change the grain** from "one row per snapshot appearance" to "one row per real-world
   event" (dedupe).
2. **Enforce quality** with explicit rules, and *keep the rejects* so quality is measurable.
3. **Attach meaning** by joining the live data to the schedule, so a bus position becomes
   "bus 4353 on the 07:24 trip of route 100001", with a known planned timetable.

## 2. The tables

### `scheduled_stop_times` (built) — the plan

**One row =** one scheduled stop visit: this trip is supposed to be at this stop at this
time on this service date. About 449K rows per weekday *(dev, 2026-10-15)*.

Key columns: `service_date`, `trip_id`, `route_id`, `direction_id`, `stop_sequence`, `stop_id`,
`scheduled_arrival_ts`, `scheduled_departure_ts` (real UTC timestamps; after-midnight and
daylight-saving handled), `feed_version`.

**Why built this way:** the raw schedule says "trip T stops at S at 25:10:00" and knows
nothing about dates. The same trip runs on many dates. Materializing one row per *date* turns
"what was planned" into a plain table you can join to what happened, with the timezone math
done once and tested. Only dates with realtime data are expanded (decision 0006).

### `scheduled_trips` (built) — which trips run on which dates

**One row =** one scheduled trip on one service date (about 13K a day): `service_date`,
`feed_version`, `trip_id`, `route_id`, `direction_id`, `service_id`. It answers "is this trip
scheduled today" without the 450K-row stop-level table, and it is built through **tomorrow**
so a new service date is never unknown. Dates from today on are rebuilt each run, because a
newly loaded schedule can change them.

### `gtfs_*` tables (built) — the reference data

Routes, trips, stops, stop times, shapes, calendars, each stamped with `feed_version`. They
let us say names, directions, and geometry, and they keep history across schedule changes.

### `vehicle_positions` (built) — what the buses did

**One row =** one distinct GPS fix of one bus, identified by `(vehicle_id, vehicle_ts)`.
Bronze had 1.46M rows *(dev)*; silver has 1.22M fixes because the same fix shows up in
several overlapping snapshots and 23K rows were rejected.

| Column | Meaning |
|---|---|
| `vehicle_id`, `vehicle_ts` | which bus, when its GPS took the fix (the identity of the row) |
| `service_date` | the schedule day the trip belongs to (a 00:30 trip still belongs to yesterday) |
| `trip_id`, `route_id`, `direction_id` | the scheduled trip the bus says it is running |
| `latitude`, `longitude` | where it was |
| `current_stop_sequence`, `stop_id`, `current_status` | progress along the trip; status 1 means stopped at the stop |
| `first_feed_ts` | when we first saw this fix (the earliest snapshot that contained it) |
| `matched_trip` | true if that trip is in `scheduled_trips` for that date; false is a mismatch; **NULL means no loaded schedule covers that date** (unknown is not the same as false). A repair step fills NULLs once a schedule covers the date. |
| `source_file`, `bronze_ingested_at` | lineage back to the raw file and bronze load |

A real example, trip 562518701 on route 100001, bus 4353, 2026-10-14 (Pacific time):

```text
time      lat       lon         seq  stop  status
07:24:02  47.64548  -122.37012  2    2020  1     <- sitting at stop 2020 (layover)
07:25:02  47.64548  -122.37012  2    2020  1
07:40:33  47.64525  -122.37016  2    2020  1     <- still there 16 minutes later
07:41:29  47.64355  -122.37019  3    2030  --    <- now moving: stop 3
07:55:20  47.619    -122.35675  17   2200  --
07:55:31  47.6188   -122.35675  18   2244  --    <- stop 18; seq never went backwards
```

### `quarantine_vehicle_positions` (built) — what we refused, and why

**One row =** one bronze row that failed a rule, with a single `reason`:

| Reason | Meaning | Share of bronze *(dev)* |
|---|---|---|
| `NO_VEHICLE_TS` | the unit reported no fix time (the placeholder rows) | 19,818 rows |
| `ZERO_COORDINATES` | latitude or longitude is 0 | 3,532 rows |
| `OUT_OF_BOUNDS` | outside a box around King County | 0 |
| `TS_IN_FUTURE` / `TS_STALE` | fix time more than 60 s ahead of, or an hour behind, the feed time | 0 |
| `MISSING_VEHICLE_ID` / `MISSING_TRIP_ID` | identifier absent | 0 |

Total 23,350 rows, **1.60 percent** of bronze (target: under about 2 percent).

**Why quarantine instead of delete or ignore:** deleting hides the problem; silently dropping
makes silver look cleaner than the source. A quarantine table makes quality a measurable
number (the rate above), shows *which* rule is firing if it spikes (a feed fault looks
different from a GPS fault), and lets us re-check the rules later because nothing is lost.

**One deliberate choice:** a trip or route that is not in the schedule is **not**
quarantined. It stays in silver with `matched_trip = false`. A schedule mismatch (for
example right after a service change) is a property of the join, and the Phase 3 metric
"match rate above 95 percent" needs those rows present to count them. Quarantining them
would make the problem disappear from the number that is supposed to expose it.

### `trip_update_snapshots` (built) — what Metro predicted, and for how long

**One row =** a *run*: a prediction for one stop of one trip on one service date that stayed
the same over consecutive snapshots, with `first_seen`, `last_seen` and `snapshots`. Bronze
holds one row per snapshot; about two thirds repeat, so silver keeps 30 percent of the rows
(17.4M bronze rows became 5.2M for 2026-10-14 *(dev)*).

A change in any of the predicted arrival and delay, departure and delay, the stop-level state
(skipped), the trip-level state (canceled), or the assigned `vehicle_id` starts a new run.
Including the vehicle means the moment a bus is assigned to a trip is visible. Real example,
trip 562518701, stop 18 on 2026-10-14 (Pacific time):

```text
first_seen  last_seen  predicted  delay  snapshots  vehicle
06:40:34    07:24:05   07:55:17   0      45         (none)   <- schedule only, no bus yet
07:25:29    07:41:08   07:55:17   0      17         4353     <- bus assigned (15 min before departure)
07:42:10    07:42:10   07:56:04   47     1          4353     <- the bus is running; revised each minute
07:43:12    07:43:12   07:55:46   29     1          4353
```

Why runs and not snapshots: **a third of the rows** (25.6M against 76.6M in prod, 2026-10-16),
and nothing is lost, because a run still says exactly what Metro was saying and during which
stretch. Only consecutive repeats collapse: a value that returns later is a new run, and a
prediction not seen for more than an hour also starts a new run (decision 0010). Fewer rows
make joins and scans cheaper, but **not the table smaller in bytes**: it is 348 MB against
313 MB for bronze, because Parquet's columnar compression already absorbs repeated values
and silver adds timestamp columns. Do not count on this step to save storage.

### `stop_visits` (built) — when each bus reached and left each stop

**One row =** one *scheduled* stop of one trip on one service date (about 449K a weekday),
whether or not a bus was seen. Columns: `sched_arr`, `sched_dep`; `arr_est`, `arr_lo`, `arr_hi`,
`arr_method` and the same for departure; `arr_delay_s`, `dep_delay_s` (estimate minus schedule,
early is negative); evidence `advance_ts`, `dwell_first`, `dwell_last`; `n_fixes`.

**Why every scheduled stop, and why intervals:** a stop nobody observed is still a fact (the
planner cares whether service ran), so it keeps a row with a method saying why there is no time
(`no_fixes`, `not_reached`, `before_first_fix`). And a time we infer from fixes 20 seconds apart is
not an instant, so each estimate carries the interval between the fixes that bracket it.

**How it is inferred, in one paragraph:** each GPS fix is placed on the route as a distance along
it; the arrival is when that distance passes the stop point (interpolated between two fixes) and
the departure when it passes 60 ft beyond. The feed's own "next stop" field and its stopped
reports are used as supporting evidence, not as the answer. Details: decision 0011.

**Full-day coverage (prod, 2026-10-14):** 86.0 percent of scheduled stop visits have an arrival
estimate; 8.6 percent were never reached in the data (mostly trip ends), 4.0 percent were passed
before the bus was first seen, 1.4 percent are trips with no fixes.

**Accuracy:** estimated from GPS, typically within about 15 seconds, always with an interval; not validated
against ground truth. The error budget is in decision 0011 (interval width, sensitivity of the arrival rule,
where dwelling buses sit relative to the stop).

### `alert_versions` (still to build)

One row per version of an alert (alerts repeat every pull). The enhanced alert feed carries
creation and modification times, which makes versions directly observable.

## 3. What you can do with these tables

Every use below is something silver makes *simple*; with bronze each needs the cleanup first.

| Purpose | How silver is used |
|---|---|
| **Trust the data** | `quarantine` rate and `matched_trip` rate are health metrics: if the match rate drops after a schedule change, you see it immediately. |
| **Where is every bus right now** (Phase 4) | latest row per `vehicle_id` in `vehicle_positions`; no duplicates to filter out. |
| **Did the bus run at all** | compare distinct scheduled trips per hour (`scheduled_stop_times`) to trips seen in `vehicle_positions`. Measured 2026-10-14: seen trips matched scheduled trips within about 5 percent in 23 of 24 Pacific hours (82 scheduled vs 83 seen at 2 AM, 1,462 vs 1,479 at 5 PM). The exception was 3 AM, 60 scheduled vs 47 seen, the hour of the feed's nightly empty window. A shortfall means cancelled service or a feed outage; this is the basis for a feed-health alert. |
| **Arrival times at stops** (Phase 5) | for each trip, the fixes in time order give when the bus reached each `stop_sequence`; the first `STOPPED_AT` fix (or interpolation along the route shape) is the observed arrival. |
| **Delay** (Phase 5) | observed arrival minus `scheduled_arrival_ts` from `scheduled_stop_times`, joined on `(service_date, trip_id, stop_sequence)`. Early is negative, late positive. |
| **Reliability metrics** (Phase 5) | on-time share, how far buses run early or late, per route, hour, and stop. |
| **Headways and bunching** | order the arrivals at one stop by time; short gaps between buses of one route are bunching. |
| **Layover and dwell** | long stretches at one stop with status stopped (as in the example above) separate layover from running time. |
| **Prediction baselines** (Phase 7) | the agency's own prediction history comes from `trip_update_snapshots`. |

Sample question you can already answer: *"For route 100001 on 2026-10-14, how many distinct
trips were running each hour?"* is one `GROUP BY` on `vehicle_positions`, with no filtering
for duplicates or placeholder rows.

## 3b. Data limits that shape which questions we can answer (measured 2026-10-15)

| Limit | Evidence | Consequence |
|---|---|---|
| **A bus often enters the feed after its trip has started** (left-censoring) | Of 5,513 trips scheduled 01:00 to 12:30 PT on 2026-10-14 (dev silver): first fix before the scheduled departure for 2,682 (49%), after it for 2,622 (48%), never seen for 209 (4%) | On-time **departure** can be measured directly for about half of trips; for the rest we can only bound it or use the delay at the first stop we did observe. Trips we can see early may differ from those we cannot (selection bias). |
| **`current_stop_sequence` is not literal** | Trip 562518701 is scheduled to start at stop 2010 (sequence 1) at 07:40:00; the bus was reported stopped at sequence 2 (stop 2020) from 07:24 | Infer arrivals and departures from position along the route shape plus status, not from the sequence field alone. |
| **Sampling** | vehicle fixes about every 20 s of our time, trip updates about every 60 s | Observed times are intervals (the bracketing fixes), not exact instants; label the method and the width. |
| **Short history** | collection began 2026-10-13 21:43 UTC | Day-of-week, seasonal, and model-training questions need weeks, not days. |
| **Pre-operation predictions are the schedule** | 55% of (trip, snapshot) pairs on route 100001 on 2026-10-14 had every delay equal to zero | Prediction accuracy must be scored only after the bus is observed on the trip. |
| **Trips announced vs seen** | 2026-10-14: 12,939 scheduled, 12,661 in trip updates, 12,552 with a bus fix; 109 announced but never seen (135 when seen is counted from silver, which drops quarantined rows); 278 scheduled but never announced. Only 20 of the 135 are marked canceled. | A candidate "service delivered" measure (about 97 percent had a fix), not yet a finding. Cancellation explains only a small part of the unseen trips; the rest (115) are unexplained: untracked buses, quarantined fixes, the nightly empty window, or missed trips. |

## 4. Design rules that apply to every silver table

1. **State the grain in one sentence** ("one row is a distinct fix of one bus"). If you
   cannot, the table is not ready.
2. **A natural key defines identity**, not a snapshot id (`entity_id` changes every pull).
3. **Idempotent writes**: reruns and overlapping reads must not add rows (a `MERGE` on the
   key). Verified: rerunning the job left 1,220,692 rows, all distinct.
4. **Keep rejects, with a reason.** Never silently drop.
5. **Unknown is not false.** Use NULL when something has not been computed yet.
6. **Keep lineage** (`source_file`, `bronze_ingested_at`) so any row can be traced to a raw file.
7. **Verify against an independent computation**, not just "the job succeeded".

### `stop_visits.pointer_lead_ft` (added 2026-10-20, decision 0019)
Feet short of this stop that the bus still was when the feed's next-stop pointer first named a later
stop AND the pointer was ignored because it moved at or before the GPS lower bound of the arrival.
NULL when the pointer was used or was not in conflict. Rows built before 2026-10-20 have NULL until
rebuilt. Typical value 76 ft (median, 2026-10-17); a few thousand feet means the pointer ran far ahead.

## `weather_observations` and `weather_hourly` (built; decision 0024)

**`weather_observations`. One row =** one reading from one weather station (KSEA, KBFI or KRNT) at one
moment, about every 5 minutes, deduplicated by (station, `observed_ts`). Columns: `temp_c`, `wind_kmh`,
`gust_kmh` (mostly empty), `visibility_m`, `humidity_pct`, `pressure_pa`, `text` (for example "Light Rain"),
`precip_level` (0 none, 1 light, 2 moderate, 3 heavy), `fog`, `codes`, `source_file`. No rain amounts exist
in the source. **`weather_hourly`. One row =** one UTC hour across the stations: `n_obs`, `n_stations`, averages,
`wet_share`, `fog_share`, `weather_class` (rain, fog, dry), and `pt_date` / `pt_hour` for joining trips.
