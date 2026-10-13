# KCM GTFS-Realtime

| | |
|---|---|
| Status | **Collecting** |
| Phase | 1-2 (collect, bronze) |
| Owner | King County Metro |
| Format | Protocol Buffers (GTFS-Realtime) |
| Cadence | vehicle positions and trip updates every minute, alerts every 5 minutes |
| Profiled | 2026-10-13 |
| Target tables | `bronze.vehicle_positions`, `bronze.trip_updates`, `bronze.alerts` |

## Executive summary

KCM realtime is a live operational feed. It is not a historical database and it is not
itself a complete schedule. Each download is a snapshot of what the agency currently
knows about vehicles, predicted stop times, and service disruptions.

For a planner, the useful story is: **what is happening now, what does the agency expect
to happen next, and which service changes or disruptions are being communicated?**

For an engineer, the important design decision is: **save every snapshot before parsing
it**. The value compounds over time. One snapshot can show a map; thousands of snapshots
can show reliability, prediction accuracy, headways, missed service, and recurring delay.

## What is in the source

| Feed | One record means | Verified sample |
|---|---|---:|
| Vehicle positions | One vehicle's current reported state | 522 entities |
| Trip updates | One trip's current predicted stop times | 1,125 entities |
| Service alerts | One active or recently published disruption | 81 entities |

The sample contains 109 distinct vehicle-position routes, 112 trip-update routes, and
33,772 stop-time updates. The three feed headers were within about one minute of one
another.

## Example: vehicle position

This is a real decoded record from the fixture, simplified to the fields most useful for
people and analysis:

```text
feed_header_timestamp: 1791919712
entity_id: 1791919712_4316
vehicle_id: 4316
vehicle_label: 4316
trip_id: 562519421
service_date: 20261013
route_id: 100001
direction_id: 1
latitude: 47.6452332
longitude: -122.370163
current_stop_sequence: 2
stop_id: 2020
current_status: STOPPED_AT
vehicle_timestamp: 1791919670
occupancy_status: EMPTY
```

### What this means

This tells us that vehicle `4316` was assigned to trip `562519421`, was associated with
route `100001`, and reported itself at stop `2020`. Its coordinates can be plotted on a
map. Its stop sequence and timestamp let us reconstruct progress when combined with later
snapshots.

The route and stop IDs are identifiers, not human-readable names. We need static GTFS to
translate them into a route name, stop name, scheduled time, and route geometry.

## Example: trip update

A real first stop update from the fixture looks like this:

```text
feed_header_timestamp: 1791919752
entity_id: 1791919752_562519331
trip_id: 562519331
service_date: 20261013
route_id: 100001
direction_id: 1
stop_sequence: 1
stop_id: 2010
arrival_delay_seconds: 0
arrival_time_unix: 1791921360
departure_delay_seconds: 0
departure_time_unix: 1791921360
```

The complete trip update contains many future stop updates. In the fixture, 33,255 of
33,772 stop updates contain both absolute arrival/departure times and delay values. This
is an agency prediction, not proof that the bus actually arrived at that time.

## Example: service alert

A real alert in the fixture describes a route detour:

```text
route_id: 100489
route_type: 3
affected_stop_ids: 48920, 48925, 50466, 50468, 47275, 47280, 48972, 48974
cause: OTHER_CAUSE
cause_detail: A_ROAD_CLOSURE
effect: DETOUR
effect_detail: SINGLE_ROUTE_REROUTE
severity: SEVERE
header: Route 156 will be rerouted due to a road closure.
active_period: 2026-02-09 through 2026-12-31
```

The full text also includes affected stops and rider instructions. This can become an
alert layer on a map and a context flag for route reliability analysis.

## Field map

### Vehicle positions

| Field | Meaning | Useful output |
|---|---|---|
| `vehicle.id` | Agency vehicle identifier | Vehicle tracking and continuity |
| `trip.trip_id` | Scheduled or realtime trip identifier | Join to schedule and predictions |
| `trip.route_id` | Route identifier | Route map and route metrics |
| `trip.direction_id` | Direction within route | Direction-specific metrics |
| `trip.start_date` | Service date | Trip-instance matching |
| `position.latitude/longitude` | Current location | Live map and route adherence |
| `current_stop_sequence` | Current schedule position | Trip progress |
| `stop_id` | Current or next stop identifier | Stop-level map and joins |
| `current_status` | Incoming, stopped, or in transit | Arrival inference candidates |
| `timestamp` | Vehicle measurement time | Ordering and freshness |
| `occupancy_status` | Coarse occupancy state, if published | Possible crowding context |

### Trip updates

| Field | Meaning | Useful output |
|---|---|---|
| `trip.trip_id` | Trip being updated | Prediction identity |
| `trip.start_date` | Service date | Point-in-time joins |
| `vehicle.id` | Vehicle serving the trip | Link prediction to vehicle |
| `stop_time_update.stop_id` | Predicted stop | Stop-level arrival estimates |
| `stop_sequence` | Position within trip | Ordered trip timeline |
| `arrival.time` | Predicted or measured absolute time | ETA display and labels |
| `arrival.delay` | Deviation from schedule, seconds | Delay charts and baselines |
| `departure.time/delay` | Departure prediction | Dwell and departure analysis |
| `TripUpdate.timestamp` | Time prediction was generated | Leakage-safe model evaluation |
| `schedule_relationship` | Scheduled, canceled, skipped, etc. | Service delivery and exceptions |

### Alerts

| Field | Meaning | Useful output |
|---|---|---|
| `informed_entity` | Affected agency, route, trip, or stop | Spatial and route joins |
| `active_period` | When the alert applies | Time-window joins |
| `cause` | Broad reason category | Categorized reporting |
| `effect` | Service consequence | Detour, no service, delays |
| text fields | Human-readable explanation | Planner and rider context |
| `url` | More information | Evidence link |
| `severity_level` | Reported severity | Prioritization |

## What we can do with KCM alone

### Live and near-real-time

- Plot buses on a map.
- Show route, direction, current stop, trip, and last update time.
- Show agency-predicted arrival times at stops.
- Show predicted delay for upcoming stops.
- Count active vehicles by route or direction.
- Display current alerts, detours, stop closures, and cancellations when published.
- Report feed freshness and collection gaps.

### After collecting snapshots

- Reconstruct vehicle paths and trip progress.
- Compare agency predictions with later agency predictions.
- Measure how often predictions change and by how much.
- Identify persistent gaps between vehicles on a route.
- Estimate which trips were observed, not observed, canceled, or ambiguous.
- Build a history of disruptions and their affected routes/stops.

## What KCM cannot answer alone

- Human-readable route or stop names, unless joined to static GTFS.
- Scheduled arrival time for every stop, unless static GTFS is loaded.
- True observed arrival time from one snapshot.
- Exact passenger counts or boardings.
- Whether a missing vehicle was canceled, inactive, or simply not tracked.
- Whether traffic, weather, or an incident caused a delay.
- Whether service is bus, rail, ferry, or another mode without static `route_type`.

## Observed collection behavior

Measured on live collected files (2026-10-13), see [collection pipeline](../collection_pipeline.md)
and [decision 0002](../decisions/0002-storage-and-dedupe-strategy.md):

- Parser test fixtures are in `tests/fixtures/*.pb`.
- A snapshot is one instant, not a time window; the next pull replaces it at the source.
- Vehicle positions: about 560 vehicles, about 98 percent with a new position time each pull.
- Trip updates: about 1,190 trips and 35,500 stop predictions per snapshot; about 67 percent
  of predictions are identical to the previous pull. Predictions reach up to about 3 hours ahead.
- Alerts: about 80, almost all unchanged between pulls.
- The feed regenerates about every 21 seconds and buses report about every 16 seconds, so
  one-minute polling captures roughly one in three feed versions (see the collection
  pipeline doc, sections 1b and 7).

## Engineering data model

The raw layer should preserve each original protobuf file. Bronze parsing should produce
append-only typed rows:

```text
bronze.vehicle_positions
bronze.trip_updates
bronze.alerts
```

Every parsed row should retain:

- `source_file`
- `ingested_at`
- `feed_header_ts`
- the source entity ID
- optional-field presence where absence has meaning

Silver tables should deduplicate and validate rows without destroying the raw history.
Gold tables can then expose `live_vehicles`, `route_status_15min`, `feed_health`,
`stop_delay`, and later reliability metrics.

## Quality checks

Before publishing metrics, check:

- feed header timestamp is present and reasonably fresh;
- `entity_id` is a per-snapshot label (`<snapshot epoch>_<n>`), not a stable key; dedupe vehicles on `vehicle_id` + `vehicle_ts` (about 5 vehicle IDs per snapshot appear twice);
- coordinates are valid and within the expected service area;
- vehicle and trip IDs are not unexpectedly missing;
- stop sequences increase plausibly over time;
- trip IDs match the active static GTFS version;
- predictions are evaluated using only snapshots available at the prediction time;
- alert time ranges and affected entities are preserved;
- missing data is reported separately from zero delay or zero service.

## Product story

A useful planner-facing presentation can move from broad to specific:

1. **Where:** Which routes, stops, or segments are unreliable?
2. **When:** Is the issue concentrated in commute periods, weekends, or special events?
3. **What is happening now:** Where are buses, how fresh is the data, and what alerts are active?
4. **Why might it be happening:** Is there a detour, recurring slow segment, weather event, or nearby incident?
5. **Who is affected:** Which high-ridership routes or stops carry the largest rider impact?
6. **What should change:** Would added runtime, a schedule adjustment, or a service intervention help?

The first three questions are supported by KCM realtime plus static GTFS. The remaining
questions require historical collection and additional sources. The interface should label
those distinctions clearly so a plausible map does not get mistaken for causal evidence.

## Related reports

- [Data report index](README.md)
- [Static GTFS](static_gtfs.md)
- [Collection pipeline](../collection_pipeline.md)
- [Free-tier limits](../limits.md)
