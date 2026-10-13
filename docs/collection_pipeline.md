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
| Vehicle positions | `vehiclepositions.pb` | where each bus is now (lat/lon, speed, bearing, trip, route, stop) | every minute |
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

Observed lag from the feed header to our landing was 4 to 13 seconds.

### Vehicle positions

- About 560 entities per snapshot *(observed)*, one per active vehicle.
- Roughly 550 of those vehicles have a new `vehicle_ts` between consecutive minutes, so
  nearly every vehicle moves each pull. The rest are stale (for example, one vehicle
  stayed at the same fix for minutes).
- Individual vehicle timestamps span the last ~3 to 4 minutes, so a snapshot is not
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
2. **Feed-update gaps (the cadence mismatch):** the feed header advanced by 63 seconds
   between each of our 60-second pulls *(observed, 3 intervals)*. That suggests KCM
   regenerates the feed on a slightly slower cycle than our poll, so our samples drift
   against it. Possible effects: an occasional pull sees the same header twice, or, if
   KCM updates faster than once a minute, some intermediate updates are never seen.
   This cannot be settled from a few minutes. After a day of data, compare the count of
   distinct `feed_header_ts` values with the number of files. If duplicates are common
   or updates are missed, add the optional 30-second second fetch noted in the design
   doc.

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
