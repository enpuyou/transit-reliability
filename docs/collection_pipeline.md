# How data gets from King County Metro into Databricks

A plain-language walkthrough of the collection pipeline: what each piece is, why it
exists, what moves between pieces, and what the data looks like. Numbers marked
*(observed)* were measured on 2026-10-13 from real landed files with
`scripts/landing_report.py`.

## 1. The big picture

```
 King County Metro (S3)            Cloudflare                        Databricks
 ----------------------      ---------------------------      ------------------------------
 vehiclepositions.pb  --\    Worker "transit-collector"       Volume  (raw files, immutable)
 tripupdates.pb       ---+-->  runs every minute (cron)  --PUT-->  workspace.transit_landing.raw
 alerts.pb            --/     fetch -> upload raw bytes             |
                                                                    v  every 30 min (ingest_job)
                                                              Auto Loader + parser
                                                                    v
                                                              bronze Delta tables
                                                              (vehicle_positions, trip_updates, alerts)
```

Three separate stages, joined only by files:

1. **Source:** KCM publishes three GTFS-Realtime protobuf files that are overwritten in
   place. Each file is only "now"; the previous state is gone as soon as it updates.
2. **Collector (Cloudflare):** takes a snapshot every minute and stores it.
3. **Databricks:** turns the stored snapshots into tables, on its own schedule.

## 1a. Glossary

| Term | Meaning |
|---|---|
| **GTFS** | General Transit Feed Specification (originally "Google Transit Feed Specification"). A standard set of CSV files describing a transit agency's *schedule*: routes, stops, trips, stop times, shapes, calendars. Published by the agency, changes per service change. |
| **GTFS-Realtime** (GTFS-RT) | The companion standard for *live* data, as Protocol Buffers: vehicle positions, trip updates (predictions), and alerts. It refers to GTFS ids, so it is only fully meaningful joined to the static GTFS. |
| **Route** | A named service, for example route 100001 (a bus line). |
| **Trip** | One scheduled run of a vehicle along a route in one direction at a set time of day, for example the 8:05 am departure of route 100001 southbound. It has an ordered list of stops with scheduled times. A bus drives many trips per day; a trip is the unit predictions and delays attach to. The same `trip_id` appears every service date it runs, so it is identified together with `start_date`. |
| **Stop sequence** | The position of a stop within a trip (1, 2, 3, ...). |
| **Entity** | One item in a realtime feed (one vehicle, one trip update, or one alert). |
| **Snapshot** | One download of a feed file: the state at that instant. |
| **Service date** | The schedule day a trip belongs to; after-midnight trips belong to the previous day. |

## 1b. The nature of the source data (KCM's S3 feed)

**What it is.** KCM runs a generator (the S3 object metadata names it
`gtfsrt_tu_vp_generator`, i.e. trip updates and vehicle positions) that writes a fresh
protobuf file to an S3 bucket every few seconds. Consumers fetch it with a plain HTTP GET.
That *is* an API, just the simplest kind: one URL per feed, no query parameters, no
history endpoint. This is the normal GTFS-Realtime pattern. Static file hosting scales to
any number of apps polling at once at almost no cost to the agency, and the standard is
defined around "fetch the current file".

**Does it overwrite itself?** Yes. The same URL always returns the latest file; the
previous one is gone. S3 object versioning does not appear to be on (no version id
header), and there is no archive we can reach. This is why the project collects
continuously: history only exists from the day we started.

**Do we bump anything by visiting?** No. A GET does not change anything on KCM's side.
We only read; the version moves forward because KCM's generator rewrites the file. Each
new version has a new `Last-Modified` and `ETag` header and a new `header.timestamp`.

**How often does it update?** Measured 2026-10-13 by polling every 5 seconds for 2.5
minutes: **a new feed version about every 21 seconds** (header times 22:38:17, :38:40,
:39:01, :39:22, :39:43, :40:04, ...). Vehicle positions and trip updates come from the same
generator and share timestamps. Alerts are a separate, slower file.

**How often does each bus report?** Measured over 3,122 consecutive-fix pairs: a bus gets
a new GPS fix in the feed about **every 16 seconds (median)**; 90 percent of gaps are
under 45 seconds. About 80 percent of buses have a new fix in any given 21-second feed
version.

**How stale is a position?** Each vehicle's own timestamp is typically about 31 seconds
older than the feed header (90th percentile 37 seconds). So the "current" position is
really where the bus was about half a minute before the file was written. A few vehicles
are much older: in 12 consecutive pulls, 3 of 729 vehicles never produced a new fix (one
reported timestamp 0), meaning the tracking unit was not reporting.

**Do parked or resting buses report?** Yes, if the unit is on. In the same sample, 24
vehicles reported fresh timestamps for 12 pulls at an unchanged position with status
`STOPPED_AT` and an assigned trip, i.e. layover or dwell. A vehicle that is off or not
transmitting simply keeps its old timestamp or drops out of the feed. About 560 vehicles
are in a given snapshot, but 729 distinct ones appeared across 12 pulls, so buses enter
and leave the feed as they start and end service.

**What the feed does not provide.** In the bronze rows, `speed`, `bearing`,
`trip_update_ts`, trip-update `vehicle_id`, and `start_time` are empty. Speed must be
derived from consecutive positions.

## 2. Why this design

| Decision | Reason |
|---|---|
| Something must snapshot the feeds continuously | The feeds only show the present. There is no public history, so any day we do not collect is lost for training and analysis. |
| The collector is outside Databricks | Free Edition restricts outbound internet from Databricks, and an always-on poller would burn the daily compute quota. A Cloudflare Worker is free, runs every minute, and needs no server. |
| Databricks is *pushed to*, not pulling | The Files API accepts inbound uploads into a Unity Catalog volume. This was verified in `docs/decisions/0000-setup-notes.md`. |
| Raw bytes are stored untouched | The raw file is the source of truth. If a parsing bug is found later, we can reparse everything. |
| Collection and ingest are decoupled | Collection runs every minute for almost no cost. Ingest runs every 30 minutes and catches up from the files. If Databricks is down or out of quota, files already collected are not lost, and ingest resumes where it stopped. |
| Files are never overwritten or deduplicated by the collector | Names are unique (epoch milliseconds). Dedupe happens later using timestamps inside the data. Cloudflare's free key-value store cannot handle a write per minute, so the collector keeps no state. |

The decision record is `docs/decisions/0001-push-collectors.md`.

## 3. Stage 1: the source feeds

Base URL: `https://s3.amazonaws.com/kcm-alerts-realtime-prod/`

| Feed | File | Content | Polled |
|---|---|---|---|
| Vehicle positions | `vehiclepositions.pb` | where each bus is now (lat/lon, trip, route, stop; no speed or bearing) | three times a minute (every ~20 s) |
| Trip updates | `tripupdates.pb` | predicted arrival/departure per upcoming stop for each active trip | every minute |
| Service alerts | `alerts.pb` | detours, delays, closures with text and affected routes/stops | every 5 minutes |

Format: Protocol Buffers (GTFS-Realtime). Each file is a `FeedMessage` with a header
(including a `timestamp` for when the feed was generated) and a list of entities.

## 4. Stage 2: the Cloudflare Worker (`workers/collector`)

**What it is:** a small TypeScript program that Cloudflare runs on a schedule. It has no
server and no database.

**Every minute (`* * * * *`):**

1. Decide which sources are due: vehicle positions and trip updates always; alerts only
   when the UTC minute is divisible by 5.
2. Fetch each feed. Reject the response if it is not HTTP 200, is empty, or is an HTML
   error page. Retry once on failure.
3. Upload the exact bytes to Databricks with an HTTP `PUT`:
   `{DATABRICKS_HOST}/api/2.0/fs/files/Volumes/workspace/transit_landing/raw/<source>/dt=<UTC date>/<epoch_ms>.pb`
   authenticated with a bearer token. Retry once on failure.
4. Log one JSON line per source (`ok`, `bytes`, `path`, or `error`).
5. A failure of one source never stops the others.

**File naming.** The scheduled minute, truncated to the minute, is used for the whole run,
so all sources from the same run share one timestamp, for example `1791927780000.pb`
means 2026-10-13 21:43:00 UTC. The `dt=` folder is the UTC date.

**Configuration:**

| Item | Where | Notes |
|---|---|---|
| Cron, KCM URL, volume path, observability | `workers/collector/wrangler.toml` | committed |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | Cloudflare Worker secrets | set with `wrangler secret put`; never in the repo |
| Account login | `wrangler login` | local only |

**Footprint:** about 1.2 MB of trip updates + 54 KB of vehicle positions per minute
*(observed)*, which is roughly 1.8 GB of raw files per day. Watch this against the
Free Edition storage limits.

**What can go wrong:** an expired or revoked Databricks token (uploads return 401/403), a
KCM outage, a Cloudflare outage, or Databricks being down. The Worker has no local
buffer in v1, so failures during those periods are gaps in the data. Only the Worker
logs show the failure; there is no alert yet (planned in Phase 8).

## 5. Stage 3: Databricks

**Landing volume:** `workspace.transit_landing.raw` is plain file storage inside Unity
Catalog, addressed as `/Volumes/workspace/transit_landing/raw/...`. It is shared by dev
and prod.

**Ingest job (every 30 minutes in prod; paused in dev):** `bundle run ingest_job`
runs `transit_reliability.entrypoints.jobs:ingest_bronze` once per source:

1. **Auto Loader** (`cloudFiles`, `binaryFile`) lists the source folder and picks up only
   files it has not seen. Which files it has seen is stored in a **checkpoint** in
   `workspace.transit_ops.state/checkpoints/bronze_<source>`.
2. In `foreachBatch`, each file's bytes are parsed by the pure functions in
   `gtfs_rt/parse.py` into rows (`mapInPandas`).
3. Rows get `source_file` and `ingested_at` and are appended to a Delta table. The write
   is tagged with a transaction id so a retried batch cannot append twice.
4. `availableNow` makes the stream process everything pending and then stop, so the job
   uses compute for about a minute and is then done.

**Result:** `bronze.vehicle_positions`, `bronze.trip_updates`, `bronze.alerts` (dev
copies are under `dev_transit_bronze`). Rerunning with no new files changes nothing
(verified).

## 6. What differs between pulls, and what time each covers

All feeds are a **snapshot of the present**, not a time window. A pull does not cover a
period; it describes one instant, and the next pull replaces it.

### Timestamps you will see

| Field | Meaning |
|---|---|
| Landing file name (`<epoch_ms>.pb`) | when the Worker ran (our clock, truncated to the minute) |
| `feed_header_ts` | when KCM generated the feed |
| `vehicle_ts` | when that bus last reported its GPS fix |
| `trip_update_ts` | when KCM last updated that trip's predictions |
| `arrival_time` / `departure_time` | predicted time at a stop (future), unix seconds |
| `ingested_at` | when Databricks wrote the row |

Observed lag from the feed header to our landing was 1 to 13 seconds, but a vehicle's own
fix is typically about 30 seconds older than the header (see 1b).

### Vehicle positions

- About 560 entities per snapshot *(observed)*, one per active vehicle.
- About 98 percent have a new `vehicle_ts` between our consecutive one-minute pulls.
  Buses report every ~16 seconds, so we see about every fourth fix per bus (see section 7).
- Within one snapshot, vehicle timestamps are mostly 30 to 40 seconds behind the feed
  header; a few units are minutes old or have no fix. The snapshot is therefore not
  perfectly simultaneous.

### Trip updates

- About 1,190 trips and ~35,500 stop predictions per snapshot *(observed)*; a trip has a
  median of 28 upcoming stops, up to 97.
- Every trip's `trip_update_ts` changes every pull, and the predicted times shift.
- Predicted stop times in the sampled snapshot ranged from about 37 minutes before the
  feed time to about 173 minutes (just under 3 hours) after it. The horizon varies by
  trip and should be confirmed across more samples before it is relied on.
- These snapshots are the agency-prediction baseline for the ML phase. Never overwrite
  them.
- Volume: one snapshot is ~35,000 rows, so ~50 million rows/day if every minute is
  kept. This is the largest cost driver in the project. Silver may need to keep only
  changed predictions or a shorter horizon.

### Alerts

- ~80 alerts *(observed)*, fetched every 5 minutes. Most alerts persist across pulls and
  only change when KCM edits, adds, or removes one.
- `active_period` is when the alert applies. The sampled alerts started as early as
  2026-02-09, and some have no end date. The feed lists everything currently active, not
  only new things.
- Because most rows repeat between pulls, bronze alerts contain many duplicates by
  design; silver should collapse them into alert versions.

### Identifiers: things that are not what they seem

- **`entity_id` is not a stable key.** It looks like `<snapshot epoch>_<n>` and changes
  every pull. Do not join or dedupe on it. Use `vehicle_id` + `vehicle_ts` for vehicles
  and `trip_id` + `start_date` + `stop_sequence` + `feed_header_ts` for predictions.
- **Duplicates exist inside one snapshot:** about 5 vehicle IDs appeared twice in a
  single vehicle-positions file *(observed)*. Silver must dedupe.

## 7. Do we have gaps?

Two different kinds:

1. **Collection gaps:** minutes where no file landed. At the time of writing, the four
   vehicle and trip-update files landed at exactly 60-second spacing from 21:43 to 21:46
   UTC with no missing minute. Only a few minutes of data exist so far, so this says very
   little. The Phase 1 exit test is 24 hours with no gap over 5 minutes. Use
   `scripts/landing_report.py` or the SQL in `docs/commands.md` to check.
2. **Sampling gaps (the important one):** the feed produces a new version every ~21
   seconds, and each bus reports every ~16 seconds, but we pull once per minute. We
   therefore capture only about one in three feed versions and about one in four fixes
   per bus. The other versions are overwritten at the source before we see them and
   cannot be recovered. (An earlier note here said the header advanced 63 seconds per
   pull and suspected a slow feed cycle. That was a sampling artifact: 63 seconds is
   three 21-second versions.) What this costs:
   - Positions between our samples are unknown. At bus speeds a vehicle can pass one to
     two stops per minute, so a stop visit can fall entirely between two pulls. Arrival
     times must be inferred by interpolating along the route shape (Phase 5), and short
     dwells are the hardest to see.
   - Trip-update predictions that changed and changed back between pulls are not seen.
   - Delay is still measurable per sampled instant, but timing error is up to about 30
     seconds on top of the feed's own ~30 second position delay.

   Options, with costs (decision pending; see section 9):
   - Poll vehicle positions every 20 seconds (small, 54 KB) and keep trip updates at one
     minute. About 3x vehicle files, little extra storage, little extra CPU.
   - Also poll trip updates every 20 seconds. Gives all predictions but about 3x the
     raw volume (about 5 GB per day) and likely exceeds the Free-plan 10 ms Worker CPU
     limit (measured 4 to 5 ms per run today).

Expected unavoidable gaps: KCM outages, token expiry, Cloudflare or Databricks outages,
and any time before collection began (2026-10-13 about 21:43 UTC). There is no backfill
for the past.

## 8. How to check each stage

See `docs/commands.md` for every command. In short:

| Question | Where |
|---|---|
| Is the Worker running? | `npm run tail` in `workers/collector`, or the Cloudflare dashboard |
| Are files landing? | `databricks fs ls dbfs:/Volumes/workspace/transit_landing/raw/...` |
| Is there a gap or lag? | `scripts/landing_report.py` on downloaded files, or the SQL gap check |
| Did ingest process everything? | `databricks jobs list-runs`, then the bronze row counts |

## 9. Example of the data at each stage

Real records captured 2026-10-13 (vehicle 4316, route 100001).

### Stage A: the file in the landing volume

`workspace.transit_landing.raw` is a **volume**: a folder in Databricks managed storage
that holds plain files. Nothing is a table here.

```text
/Volumes/workspace/transit_landing/raw/
  vehicle_positions/dt=2026-10-13/1791931260000.pb      (54 KB, one snapshot)
  vehicle_positions/dt=2026-10-13/1791931320000.pb
  trip_updates/dt=2026-10-13/1791931260000.pb           (1.3 MB)
  alerts/dt=2026-10-13/1791931200000.pb
```

The file name is the Worker's scheduled time in epoch milliseconds (1791931260000 =
2026-10-13 22:41:00 UTC). It is binary protobuf, unreadable without a decoder.

### Stage B: the same file decoded (protobuf as text)

```text
header { gtfs_realtime_version: "2.0"  incrementality: FULL_DATASET  timestamp: 1791931267 }
entity {
  id: "1791931267_4316"
  vehicle {
    trip { trip_id: "562519821"  start_date: "20261013"  route_id: "100001"  direction_id: 1 }
    position { latitude: 47.618584  longitude: -122.35208 }
    current_stop_sequence: 19
    current_status: STOPPED_AT
    timestamp: 1791931233
    stop_id: "2220"
    vehicle { id: "4316"  label: "4316" }
    occupancy_status: EMPTY
  }
}
```

Read it as: at feed time 1791931267, bus 4316, running trip 562519821 of route 100001,
was stopped at stop 2220 (the 19th stop of the trip), last fix 34 seconds earlier.

### Stage C: the bronze table row

`workspace.<schema_prefix>_bronze.vehicle_positions` is a **Delta table** (a database
table, queryable with SQL). One file becomes one row per vehicle. This row is from the
dev smoke fixture; real collected files appear after the prod or dev ingest runs:

```text
entity_id 1791919712_4316        feed_header_ts 1791919712     vehicle_id 4316
trip_id 562519421                route_id 100001               direction_id 1
start_date 20261013              latitude 47.645233            longitude -122.370163
current_stop_sequence 2          stop_id 2020                  current_status 1 (STOPPED_AT)
vehicle_ts 1791919670            occupancy_status 0 (EMPTY)    bearing NULL    speed NULL
source_file .../vehiclepositions-smoke.pb                      ingested_at 2026-10-13T21:27:17Z
```

A trip update row (one per predicted stop) looks like:

```text
trip_id 562519331   route_id 100001   stop_sequence 1   stop_id 2010
arrival_delay 0   arrival_time 1791921360   departure_delay 0   departure_time 1791921360
feed_header_ts 1791919752   source_file .../tripupdates-smoke.pb
```

### What changes between stages

| Stage | Form | Question it answers |
|---|---|---|
| A. Volume file | binary, one per snapshot, immutable | "what exactly did KCM publish at that minute?" (source of truth) |
| B. Decoded | nested messages, in memory only | debugging |
| C. Bronze table | flat typed rows, append-only | "which vehicles/predictions were reported when?" |
| Later: silver, gold | deduped, joined to schedule, metrics | "how late was this trip?" |

## 10. Early arrivals, and what the feed can say about them

Early service is a miss for riders, and the design must treat it as one. What we can say
now, from the 2026-10-13 trip-update snapshot (38,866 predicted stop times, agency
predictions, not observed arrivals):

| Predicted delay at the stop | Share |
|---|---|
| more than 60 s early | 2.3 percent |
| within the on-time window (60 s early to 5 min late) | 87.6 percent |
| more than 5 min late | 10.1 percent |

Caveats: the median predicted delay is exactly 0, because predictions for distant stops
are often just the schedule, so this under-states how early buses really run. True early
arrival and early departure from a timepoint can only be measured by comparing the
*observed* arrival (inferred from positions, Phase 5) with the scheduled time (static
GTFS, Phase 3). The delay field already tells us the sign: negative is early. About 500
stop updates in the snapshot are marked `SKIPPED`.

Metric definitions in `design_doc.md` already treat more than 60 s early as not on time.
But "rider-minutes lost" currently counts only lateness (`max(delay, 0)`), so an early
departure that strands waiting riders is not weighted. Add an early-departure metric when
`docs/metrics.md` is finalized.

## 11. Polling frequency (decided: option 2, see decision 0003)

Sections 1b and 7 show that one-minute polling misses most of the feed's updates. The
options were: keep 60 s; vehicle positions every 20 s with trip updates every 60 s; or
everything every 20 s. Option 2 was implemented on 2026-10-13: the Worker still fires once
a minute (the cron minimum), then fetches vehicle positions at 0 s, 20 s, and 40 s inside
the run, waiting between fetches. Waiting is wall time, not CPU time. A live check showed
files landing about every 21 seconds, each a distinct feed version.

Remaining caveat: CPU per run rose from 4-5 ms to 6-10 ms against the Free-plan 10 ms
limit. See `docs/decisions/0003-vehicle-polling-every-20-seconds.md` and `docs/limits.md`.
Sections 4 and 7 above describe the original one-minute design; where they say "every
minute" for vehicle positions, read "three times a minute since 2026-10-13 23:30 UTC".
