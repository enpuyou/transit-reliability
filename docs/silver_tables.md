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
| `matched_trip` | true if that trip is scheduled on that date; false is a mismatch; **NULL means the date has not been expanded yet** (unknown is not the same as false) |
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

### Still to build

- `trip_update_snapshots`: **one row = one distinct prediction value** for a stop on a trip,
  with `first_seen` and `last_seen` feed times, instead of one row per snapshot. About 67%
  of predictions repeat between pulls, so this shrinks the biggest table (67M rows) a lot
  while keeping every change (decision 0002). Its purpose is the "what did KCM predict at
  time t" baseline that Phase 7 beats.
- `alert_versions`: one row per version of an alert (alerts repeat every pull).

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
