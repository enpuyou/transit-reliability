# Seattle Transit Reliability: Development Plan

Working name: `transit-reliability`. Rename freely.

This document is the source of truth for scope, architecture, setup, and phased delivery. It is written to be pasted into a repo and worked through with a coding agent. Each phase ends with manual checks you run yourself before moving on.

---

## 0. Project summary

**Customer:** a King County Metro (KCM) service planner.

**Questions the system answers:**
1. Where and when is service late, bunched, or missing?
2. Why? (weather, events, traffic, incidents, unrealistic schedule)
3. How many riders were affected? (rider-minutes lost)
4. What should change? (segment-level schedule recommendations with evidence)
5. Can we predict delays 5 to 30 minutes ahead better than the agency's own predictions?

**Headline pitch:** a near-real-time system that tells planners where unreliability costs riders the most, why it happens, and what schedule changes would help, with predictions benchmarked against the agency's published predictions.

**Non-goals (v1):** sub-minute latency, rider-facing trip planner, Link light rail, multi-agency coverage, true environment isolation (Free Edition has one workspace).

---

## 1. Hard constraints (Databricks Free Edition)

Verified from Databricks docs (Free Edition limitations page, last updated Sep 2026). Re-check if anything behaves differently.

| Constraint | Design consequence |
|---|---|
| Serverless compute only | No custom clusters. Jobs use serverless with an `environments` block for pip deps. |
| Outbound internet restricted to "trusted domains" | Do NOT fetch external feeds from Databricks. External collectors push data in. |
| Daily compute quota; overrun shuts compute off for the rest of the day | Micro-batch every 15 to 30 min, measure usage, keep jobs lean. Collectors keep buffering if Databricks is down. |
| One SQL warehouse, 2X-Small | Public site must never query the warehouse. Jobs export JSON; site reads exports. |
| Max 5 concurrent job tasks | Keep job DAGs narrow. |
| One active Lakeflow pipeline per type | Use plain Jobs + Structured Streaming (`availableNow`), not Lakeflow pipelines. |
| Apps stop after 24h, require workspace login | Public site hosted outside Databricks. |
| Serverless streaming supports only `availableNow` / `once` triggers | All streaming is triggered micro-batch. |
| Inactive accounts may be deleted | Everything reproducible from repo (bundle + SQL setup scripts). |
| Non-commercial use only | Fine for a portfolio project. |

---

## 2. Architecture

```
                         EXTERNAL SOURCES
  KCM GTFS-RT (S3, ~30s)   WSDOT API (90s-2min)   Seattle Fire 911 (5 min)   NOAA (hourly)
            \                     |                        |                     /
             v                    v                        v                    v
  +--------------------------------------------------------------------------------+
  | Collector A: Cloudflare Worker, cron every minute (high-frequency sources)      |
  |   fetch -> upload raw bytes to UC Volume via Databricks Files API (PUT)         |
  +--------------------------------------------------------------------------------+
  +--------------------------------------------------------------------------------+
  | Collector B: GitHub Actions, daily cron (large/slow sources)                    |
  |   static GTFS zip, ridership, event schedules -> Volume via Databricks CLI      |
  +--------------------------------------------------------------------------------+
                                       |
                                       v
  +---------------------- Databricks Free Edition (one workspace) -----------------+
  | /Volumes/workspace/transit_landing/raw/<source>/dt=YYYY-MM-DD/<epoch>.<ext>     |
  |                                                                                 |
  | Job: ingest (every 15-30 min)                                                    |
  |   Auto Loader (binaryFile, availableNow) -> bronze -> silver -> gold            |
  |   -> export small JSON snapshots to /Volumes/.../exports/                       |
  | Job: static_refresh (daily) -> GTFS schedule tables, ridership, events          |
  | Job: train (daily/weekly) -> MLflow -> UC model registry (@champion)            |
  | Job: score (with ingest) -> predictions table                                   |
  +--------------------------------------------------------------------------------+
                                       |
                                       v  (Files API GET, no warehouse needed)
  +--------------------------------------------------------------------------------+
  | Publisher: Cloudflare Worker, cron every 15 min                                 |
  |   read export JSON from Volume -> store in KV/R2 -> serve /api/* publicly       |
  |   health check: alert webhook if data is stale                                  |
  +--------------------------------------------------------------------------------+
                                       |
                                       v
  +--------------------------------------------------------------------------------+
  | Public site: Next.js on Cloudflare Pages (reads /api/*)                         |
  |   live map, reliability report, schedule findings, model scorecard,             |
  |   pipeline health, methodology                                                  |
  +--------------------------------------------------------------------------------+
```

**Why this shape (record in `docs/decisions/`):**
- **Push, not pull:** Databricks outbound is restricted; inbound Files API works.
- **Decoupled collection:** collectors keep writing raw files even when Databricks is down or over quota; ingest catches up later.
- **No warehouse in the serving path:** exports are files; public traffic never wakes compute.
- **Two collectors:** Workers are cheap and run every minute but have memory/CPU limits; GitHub Actions handles large daily downloads.

---

## 3. Data sources

| Source | Cadence | Access | Status | Phase |
|---|---|---|---|---|
| KCM vehicle positions | poll 1 min | `https://s3.amazonaws.com/kcm-alerts-realtime-prod/vehiclepositions.pb` | URL verified via MobilityDatabase; no key seen | 1 |
| KCM trip updates | poll 1 min | expected `.../kcm-alerts-realtime-prod/tripupdates.pb` | **verify in Phase 0** (listed on MobilityDatabase as mdb-1540) | 1 |
| KCM service alerts | poll 5 min | `.../kcm-alerts-realtime-prod/alerts.pb` | verified | 1 |
| KCM static GTFS | daily | `https://www.soundtransit.org/GTFS-KCM/google_transit.zip` | verified on Sound Transit OTD page | 3 |
| NOAA weather | hourly | `api.weather.gov` (no key, requires User-Agent with contact) | not verified here | 6 |
| Event schedules | daily | MLB Stats API, team sites, hand-maintained seed CSV | partially manual | 6 |
| KCM ridership (APC) | per service change | data.kingcounty.gov Metro Rider Dashboard; seattletransitridership.com | verified to exist; format TBD | 6 |
| WSDOT traffic flow, travel times, highway alerts | poll 2-5 min | WSDOT Traveler Information API, free access code by email | verified | 6 |
| Seattle Fire 911 dispatches | poll 5 min | data.seattle.gov dataset `kzjm-xkqj` (Socrata) | verified, updated every 5 min | 6 |
| Census ACS / equity areas | one-time | Census API, King County | stretch | 9 |

**Before publishing anything:** read KCM's terms of use for the realtime feeds and record the required attribution in `docs/data_sources.md`.

---

## 4. Tech stack

| Area | Choice | Notes |
|---|---|---|
| Language | Python 3.12 (data/ML), TypeScript (workers, site) | |
| Python packaging | `uv`, single package `transit_reliability` built as a wheel | |
| Databricks deploy | Databricks Asset Bundles (`databricks.yml`) | targets: `dev`, `prod` |
| Local dev | Databricks Connect (serverless) for interactive runs; local PySpark for unit tests | `databricks-connect` and `pyspark` conflict in one env; use separate dependency groups / venvs |
| Protobuf parsing | `gtfs-realtime-bindings` + `protobuf` | parse inside Spark via `mapInPandas` |
| Storage | Delta tables in `workspace` catalog | Free Edition default catalog; avoid creating catalogs unless confirmed allowed |
| ML | LightGBM, scikit-learn, MLflow, UC model registry | |
| Collectors / publisher | Cloudflare Workers + Wrangler | free tier |
| Daily collector | GitHub Actions | |
| Site | Next.js (static export) on Cloudflare Pages, MapLibre GL for maps | |
| Lint/format/types | ruff, mypy (Python); eslint, tsc (TS) | |
| Tests | pytest, chispa (Spark DataFrame asserts); vitest for workers | |
| CI/CD | GitHub Actions | |
| Commit style | Conventional commits (commitizen) | |

---

## 5. Repository layout

```
transit-reliability/
├── DEVELOPMENT_PLAN.md
├── README.md
├── pyproject.toml
├── databricks.yml                     # bundle root, targets dev/prod
├── resources/
│   ├── ingest_job.yml
│   ├── static_refresh_job.yml
│   ├── train_job.yml
│   └── setup_job.yml                  # one-off: schemas, volumes
├── src/transit_reliability/
│   ├── config.py                      # schema names, paths from bundle vars
│   ├── io/                            # volume paths, table names
│   ├── gtfs_rt/parse.py               # protobuf -> rows (pure functions)
│   ├── gtfs_static/load.py            # zip -> tables, service-date expansion
│   ├── bronze/                        # auto loader readers
│   ├── silver/                        # dedupe, typing, trip matching, quarantine
│   ├── arrivals/infer.py              # position -> stop arrival inference
│   ├── gold/                          # metrics: OTP, headways, bunching, delivery, runtimes
│   ├── context/                       # weather, events, wsdot, fire911, ridership
│   ├── ml/                            # features, train, evaluate, score
│   ├── export/                        # gold -> JSON snapshots
│   ├── quality/                       # expectations, feed health
│   └── entrypoints/                   # thin CLI entrypoints called by jobs
├── sql/
│   └── setup.sql                      # schemas, volumes (idempotent)
├── tests/
│   ├── fixtures/                      # real sample .pb files, small GTFS subset
│   ├── unit/
│   └── integration/
├── workers/
│   ├── collector/                     # cron every minute
│   └── publisher/                     # cron every 15 min + public API
├── site/                              # Next.js app
├── scripts/
│   ├── upload_static.py               # used by GitHub Actions daily collector
│   └── smoke_check.py                 # manual verification helper
├── docs/
│   ├── decisions/                     # ADRs: 0001-push-collectors.md, ...
│   ├── data_sources.md
│   ├── metrics.md                     # metric definitions
│   ├── runbook.md
│   └── brief.md                       # one-page customer problem brief
└── .github/workflows/
    ├── pr.yml
    ├── deploy.yml
    └── daily_collect.yml
```

---

## 6. Naming and environments

One workspace, two bundle targets, separated by schema prefix.

- Catalog: `workspace` (Free Edition default)
- Shared landing (written by collectors, read by both targets): schema `transit_landing`, volume `raw`
- Per-target schemas: `${prefix}_bronze`, `${prefix}_silver`, `${prefix}_gold`, `${prefix}_ml`, `${prefix}_ops`
  - `prod` target: prefix `transit`
  - `dev` target: prefix `dev_<username>_transit`
- Checkpoints and exports: volume `${prefix}_ops.state` (`/checkpoints/...`, `/exports/...`)

Bundle `mode: development` on the dev target prefixes job names and pauses schedules automatically. Prod schedules are active.

Be honest in docs: this is target separation inside one workspace, not real environment isolation.

---

## 7. Manual setup (do this first)

Track each item as a checkbox. Record any surprises in `docs/decisions/0000-setup-notes.md`.

### 7.1 Accounts

- [ ] **Databricks Free Edition**: sign up at databricks.com/learn/free-edition. Note your workspace URL (e.g. `https://dbc-xxxx.cloud.databricks.com`).
- [ ] Optional: click **Verify identity** (LinkedIn) in the workspace top bar to raise some limits.
- [ ] **GitHub**: create empty repo `transit-reliability`.
- [ ] **Cloudflare**: free account (Workers, KV, Pages).
- [ ] **WSDOT Traveler API**: request access code by email at `https://wsdot.wa.gov/traffic/api/` (needed in Phase 6; request now, it is instant).
- [ ] Optional: **Seattle open data app token** (Socrata) for higher rate limits.
- [ ] Optional: a Discord or Slack webhook URL for alerts.

### 7.2 Local tools

```bash
# Python tooling
curl -LsSf https://astral.sh/uv/install.sh | sh

# Databricks CLI (macOS shown; see Databricks docs for other OS)
brew tap databricks/tap && brew install databricks

# Node 20+ and Wrangler
brew install node
npm install -g wrangler

# GitHub CLI (optional)
brew install gh
```

### 7.3 Databricks auth and token

- [ ] In the workspace: User settings > Developer > Access tokens > generate a token (set an expiry; note the date in your calendar to rotate).
- [ ] Local CLI login:
  ```bash
  databricks auth login --host https://<your-workspace-host>
  databricks current-user me      # should print your user
  ```
- [ ] Export for scripts:
  ```bash
  export DATABRICKS_HOST=https://<your-workspace-host>
  export DATABRICKS_TOKEN=<token>
  ```

### 7.4 Create schemas and the landing volume

Run in a Databricks SQL editor or notebook (also saved as `sql/setup.sql`):

```sql
CREATE SCHEMA IF NOT EXISTS workspace.transit_landing;
CREATE VOLUME IF NOT EXISTS workspace.transit_landing.raw;

CREATE SCHEMA IF NOT EXISTS workspace.transit_ops;
CREATE VOLUME IF NOT EXISTS workspace.transit_ops.state;
```

Bronze/silver/gold/ml schemas are created by the bundle setup job in Phase 2.

### 7.5 Feasibility checks (critical; do before writing code)

**Check A: feeds are reachable and what they contain.** From your laptop:

```bash
mkdir -p tests/fixtures
curl -sS -o tests/fixtures/vehiclepositions.pb https://s3.amazonaws.com/kcm-alerts-realtime-prod/vehiclepositions.pb
curl -sS -o tests/fixtures/tripupdates.pb      https://s3.amazonaws.com/kcm-alerts-realtime-prod/tripupdates.pb
curl -sS -o tests/fixtures/alerts.pb           https://s3.amazonaws.com/kcm-alerts-realtime-prod/alerts.pb
ls -lh tests/fixtures
```

Then decode one to inspect fields:

```bash
uv run --with gtfs-realtime-bindings python - <<'EOF'
from google.transit import gtfs_realtime_pb2
for name in ["vehiclepositions", "tripupdates"]:
    f = gtfs_realtime_pb2.FeedMessage()
    f.ParseFromString(open(f"tests/fixtures/{name}.pb","rb").read())
    print(name, "header ts:", f.header.timestamp, "entities:", len(f.entity))
    print(f.entity[0])
EOF
```

Record in setup notes:
- [ ] Trip updates URL works (if not, find the correct one on MobilityDatabase mdb-1540).
- [ ] File sizes, entity counts.
- [ ] Which fields are populated in vehicle positions: `trip.trip_id`, `trip.route_id`, `current_stop_sequence`, `stop_id`, `current_status`, `timestamp`. **This decides the difficulty of arrival inference (Phase 5).**
- [ ] Do trip updates include per-stop `arrival.delay` or only `arrival.time`?
- [ ] Download the feed twice, 30 seconds apart: does the header timestamp change? (determines useful poll frequency)

**Check B: Databricks outbound access.** In a Databricks notebook:

```python
import requests
r = requests.get("https://s3.amazonaws.com/kcm-alerts-realtime-prod/vehiclepositions.pb", timeout=10)
print(r.status_code, len(r.content))
```

Record result. Either way we keep external collectors (quota protection), but if this works, `static_refresh` can download GTFS directly.

**Check C: Files API upload works from outside.**

```bash
echo "hello" > /tmp/hello.txt
curl -sS -X PUT \
  "$DATABRICKS_HOST/api/2.0/fs/files/Volumes/workspace/transit_landing/raw/_smoke/hello.txt?overwrite=true" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  --data-binary @/tmp/hello.txt -w "%{http_code}\n"
```

- [ ] Expect HTTP 204 (or 200). Confirm the file appears in Catalog Explorer under the volume.
- [ ] If this fails, the fallback collector is GitHub Actions or a local machine running the Databricks CLI; record the decision.

**Check D: read the KCM realtime terms of use** and record attribution requirements.

### 7.6 Secrets

- [ ] GitHub repo secrets: `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- [ ] Cloudflare Worker secrets (set via `wrangler secret put` after Phase 1 scaffold): `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, later `WSDOT_ACCESS_CODE`, `ALERT_WEBHOOK_URL`.
- [ ] Never commit `.env`. Add `.env*` to `.gitignore` on day one.

**Exit criteria for setup:** checks A to D completed and recorded. Do not start Phase 1 until Check C passes or a fallback is chosen.

---

## 8. Phased delivery

Each phase: goal, build tasks, manual verification, exit criteria. Estimates assume part-time work and are rough.

**Rule: start the collector (Phase 1) as early as possible.** No public historical archive of KCM realtime data was found, so training data only exists from the day collection starts.

---

### Phase 1: Collector live (1 to 2 days)

**Goal:** raw KCM realtime files land in the Volume every minute, unattended.

**Build:**
- `workers/collector`: Wrangler project, cron `* * * * *`.
  - Fetch vehicle positions and trip updates every run; alerts when `minute % 5 == 0`.
  - Upload raw bytes to `/Volumes/workspace/transit_landing/raw/<source>/dt=<YYYY-MM-DD>/<epoch_ms>.pb` (UTC date).
  - On fetch or upload failure: log, retry once, continue (never crash the whole run for one source).
  - Do not dedupe in the Worker. KV free tier allows only about 1,000 writes/day, which a per-minute dedupe would exceed. Dedupe happens in bronze using the feed header timestamp.
  - Optional: a second fetch 30 seconds into the run if Check A showed the feed changes faster than once a minute.
- Repo scaffolding: `pyproject.toml`, ruff, mypy, pre-commit, commitizen, `.gitignore`, empty package.
- Minimal CI (`pr.yml`): ruff, mypy, pytest (even if only one placeholder test), worker typecheck.
- `docs/decisions/0001-push-collectors.md`.

**Manual verification:**
- [ ] `wrangler tail` shows successful runs each minute.
- [ ] Catalog Explorer: files accumulate under `raw/vehicle_positions/dt=<today>/`.
- [ ] After 1 hour, roughly 60 vehicle-position files exist:
  ```sql
  SELECT count(*) FROM read_files(
    '/Volumes/workspace/transit_landing/raw/vehicle_positions/dt=2026-10-13/',
    format => 'binaryFile');
  ```
- [ ] Download one file from the volume and decode it locally with the Check A script.

**Exit:** 24 hours of continuous files with no gap longer than 5 minutes.

---

### Phase 2: Bronze (2 to 3 days)

**Goal:** raw files parsed into typed rows, incrementally and idempotently.

**Build:**
- `databricks.yml` with `dev` and `prod` targets, variables for schema prefix.
- `setup_job`: creates per-target schemas and the state volume.
- `gtfs_rt/parse.py`: pure functions `FeedMessage bytes -> list[dict]` for vehicle positions, trip updates (exploded to one row per stop_time_update), alerts. Unit tested against `tests/fixtures/*.pb`.
- `bronze/`: Auto Loader readers
  - `spark.readStream.format("cloudFiles").option("cloudFiles.format","binaryFile")`
  - parse with `mapInPandas` using the pure functions
  - add `source_file`, `ingested_at`, `feed_header_ts`
  - `trigger(availableNow=True)`, checkpoint in `${prefix}_ops.state/checkpoints/<table>`
- Tables: `bronze.vehicle_positions`, `bronze.trip_updates`, `bronze.alerts`.
- `ingest_job` (task 1 = bronze). Prod schedule every 30 min to start.
- `ops.job_runs` table: one row per run with rows read, rows written, duration.

**Bronze schemas (minimum):**
- `vehicle_positions`: feed_header_ts, vehicle_ts, entity_id, vehicle_id, trip_id, route_id, direction_id, start_date, start_time, lat, lon, bearing, speed, current_stop_sequence, stop_id, current_status, source_file, ingested_at
- `trip_updates`: feed_header_ts, trip_update_ts, trip_id, route_id, direction_id, start_date, vehicle_id, stop_sequence, stop_id, arrival_delay, arrival_time, departure_delay, departure_time, schedule_relationship, source_file, ingested_at
- `alerts`: feed_header_ts, alert_id, cause, effect, header_text, description_text, active_start, active_end, informed_entities (array/struct), source_file, ingested_at

Keep every trip-update snapshot (do not overwrite). These snapshots are the agency-prediction baseline for Phase 7.

**Manual verification:**
- [ ] Run once in dev: `databricks bundle deploy -t dev && databricks bundle run ingest_job -t dev`.
- [ ] Row counts look sane:
  ```sql
  SELECT date(from_unixtime(feed_header_ts)) d, count(*) rows,
         count(DISTINCT vehicle_id) vehicles, count(DISTINCT source_file) files
  FROM dev_<you>_transit_bronze.vehicle_positions GROUP BY d ORDER BY d;
  ```
- [ ] Rerun the job immediately: row count does not change (checkpoint works, no reprocessing).
- [ ] Check job duration and note compute usage in the workspace usage page. Record baseline in `docs/runbook.md`.

**Exit:** prod ingest runs on schedule for 24 hours; no duplicate file processing; compute usage measured.

---

### Phase 3: Static GTFS and silver (3 to 5 days)

**Goal:** clean, deduplicated realtime data matched to the published schedule, with bad rows quarantined.

**Build:**
- `.github/workflows/daily_collect.yml`: download KCM GTFS zip daily, upload to `raw/gtfs_static/dt=<date>/google_transit.zip` with the Databricks CLI (`databricks fs cp`). Skip upload if the file hash matches the previous day.
- `gtfs_static/load.py` and `static_refresh_job`:
  - load `routes`, `trips`, `stops`, `stop_times`, `shapes`, `calendar`, `calendar_dates` into silver, versioned by feed date (keep history; schedules change each service change)
  - **service-date expansion:** which trips run on which date (calendar + exceptions)
  - **time handling:** GTFS times can exceed `24:00:00` (after-midnight trips belong to the previous service date); convert to timestamps in `America/Los_Angeles` correctly across DST
  - `scheduled_stop_times`: one row per (service_date, trip_id, stop_sequence) with scheduled arrival timestamp
- Silver realtime:
  - `silver.vehicle_positions`: dedupe on (vehicle_id, vehicle_ts); add service_date; drop impossible coordinates
  - `silver.trip_update_snapshots`: dedupe on (trip_id, start_date, stop_sequence, feed_header_ts)
  - match realtime `trip_id` to schedule; record `matched` flag
  - `silver.quarantine`: rows failing rules, with `reason` column
- Quality rules (`quality/`): lat/lon within King County bounding box, vehicle_ts not in the future or older than 1 hour vs feed time, trip_id present, route_id known.

**Manual verification:**
- [ ] Trip match rate:
  ```sql
  SELECT service_date, avg(CASE WHEN matched THEN 1 ELSE 0 END) match_rate
  FROM transit_silver.vehicle_positions GROUP BY service_date ORDER BY service_date;
  ```
  Target above 95%. Investigate if lower (usually a schedule version mismatch).
- [ ] Quarantine breakdown by reason; rate under ~2% or explained.
- [ ] Spot-check one route you know: pick a trip, list its positions in time order, confirm they move along the route in a plausible sequence.
- [ ] After-midnight trip check: a trip scheduled `25:10:00` appears with the correct timestamp on the previous service date.

**Exit:** match rate and quarantine rate documented and acceptable; static refresh runs daily.

---

### Phase 4: First gold tables and public site skeleton (3 to 4 days)

**Goal:** an end-to-end thin slice is publicly visible.

**Build:**
- Gold v1 (using trip-update delays as a first proxy, replaced in Phase 5):
  - `gold.live_vehicles`: latest position per vehicle with current delay
  - `gold.route_status_15min`: per route, 15-minute buckets: active vehicles, median delay, p90 delay
  - `gold.feed_health`: per source, files/hour, rows/hour, last feed timestamp, quarantine rate
- `export/`: at the end of each ingest run, write small JSON files to `${prefix}_ops.state/exports/` (`live_vehicles.json`, `route_status.json`, `feed_health.json`, `meta.json` with `as_of`).
- `workers/publisher`:
  - cron every 15 minutes: GET export files via Files API, store in KV (about 4 keys x 96 runs/day, well under the KV write limit)
  - HTTP routes: `GET /api/<name>` returns cached JSON with CORS and cache headers
  - staleness check: if `as_of` older than 60 minutes, POST to alert webhook (rate-limited to one alert per hour)
- `site/`: Next.js static export on Cloudflare Pages
  - Home: live map (MapLibre) with vehicles colored by delay, "data as of" timestamp
  - Pipeline health page
  - Footer: data attribution per KCM terms

**Manual verification:**
- [ ] Public URL loads on your phone off Wi-Fi.
- [ ] Vehicle count on the map roughly matches what you expect for time of day.
- [ ] Pick a bus visible on a transit app (OneBusAway, Transit, Google Maps); compare its position and delay with your map.
- [ ] Pause the prod ingest job for an hour: site still loads with old `as_of`; alert webhook fires.

**Exit:** public site live with data updating automatically; staleness alert proven.

---

### Phase 5: Stop arrivals and reliability metrics (1 to 2 weeks; the hardest phase)

**Goal:** observed arrival times at stops, and the core planner metrics built on them.

**Build:**
- `arrivals/infer.py`. Approach depends on Check A findings:
  - If `current_status = STOPPED_AT` with `stop_id` is reliably populated: use first STOPPED_AT ping per (trip, stop) as arrival.
  - Otherwise / additionally: project each ping onto the route shape (distance along shape), then interpolate the time the vehicle crossed each stop's shape distance between consecutive pings.
  - Handle: GPS jitter (non-monotonic distance), missed pings (cap interpolation gap, e.g. 3 minutes), layovers at terminals, vehicles reassigned mid-trip, deadheading.
  - Output `silver.stop_arrivals`: service_date, trip_id, route_id, stop_id, stop_sequence, observed_arrival_ts, method (`stopped_at` / `interpolated`), confidence.
- Gold metrics (definitions in `docs/metrics.md`):
  - `gold.stop_delay`: observed minus scheduled arrival
  - `gold.on_time_performance`: by route, direction, hour, day type. Common definition: between 1 minute early and 5 minutes late. Confirm Metro's official definition and use it.
  - `gold.headways` and `gold.bunching_events`: actual vs scheduled headway; bunching when actual headway < 25% of scheduled (tunable, document it)
  - `gold.trip_delivery`: scheduled trips vs trips observed in realtime. Classify `observed`, `not_observed`. Document that not-observed can mean canceled or untracked.
  - `gold.segment_runtimes`: travel time between consecutive timepoints vs scheduled; distribution (p50, p85, p95) by route segment, hour band, day type
- Unit tests with synthetic trajectories (straight line, jitter, gap, layover) with known expected arrival times.

**Manual verification (the important part):**
- [ ] **Ride test:** ride a bus, note actual arrival times at 5+ stops with your phone clock. Compare with `silver.stop_arrivals`. Target median absolute error under 60 seconds. Record results in `docs/metrics.md`.
- [ ] Coverage: share of scheduled stop visits with an inferred arrival. Investigate routes far below average.
- [ ] Sanity: OTP by route is in a believable range (not 0%, not 100%). Compare with any figures Metro publishes in performance reports.
- [ ] Bunching: pick one detected event, plot the two vehicles' positions over time, confirm visually.

**Exit:** arrivals validated by ride test; metrics documented; gold tables populated daily.

---

### Phase 6: Context data and the "why / who" story (1 to 2 weeks)

**Goal:** explain delays and weight them by riders.

**Build, in priority order:**
1. **Ridership:** load route- and stop-level average boardings/loads (format TBD; may be manual download per service change into the GitHub Actions collector). Compute `gold.rider_minutes_lost` = delay x estimated riders affected. This becomes the headline metric.
2. **Weather:** collector fetches NOAA hourly observations for a few Seattle stations. `silver.weather_hourly`.
3. **Events:** seed CSV (`data/events_seed.csv`) plus daily scrape where feasible: venue, start, end, expected attendance tier. `silver.events`.
4. **WSDOT:** collector polls traffic flow and highway alerts every 5 minutes (respect limits). Map flow stations to nearby route segments (spatial join, buffer distance documented).
5. **Seattle Fire 911:** collector polls Socrata every 5 minutes for new dispatches; spatial join incidents within ~150 m of a route segment and active window.
- `gold.delay_attribution`: for each significant delay episode (segment x 15-min window above threshold), flag co-occurring factors: rain, event within N minutes and M km, WSDOT StopAndGo nearby, incident nearby, chronic segment (consistently slow at that hour). Report share of delay with each flag and share unexplained. This is association, not causation; say so on the site.
- `gold.schedule_recommendations`: for each segment x hour band where scheduled runtime < observed p50, propose scheduled runtime = observed p85 (tunable) and estimate OTP if adopted. Rank by rider-minutes.
- Site pages: Reliability report (rider-weighted rankings), Schedule findings, Delay drivers.

**Manual verification:**
- [ ] Pick a known rainy day and a game day; confirm attribution flags fire on affected routes and not on unaffected ones.
- [ ] Spot-check 3 incident joins: incident location really is on/near the route.
- [ ] Top 10 schedule recommendations: open each segment's runtime distribution chart and confirm it supports the recommendation.
- [ ] Rider-weighted ranking differs from raw-delay ranking in an explainable way (busy routes move up).

**Exit:** reliability report and schedule findings live and defensible.

---

### Phase 7: Delay prediction vs. baselines (1 to 2 weeks; needs 3+ weeks of collected data)

**Goal:** predict arrival delay at downstream stops for horizons 5, 15, 30 minutes, benchmarked honestly.

**Build:**
- Problem definition: at prediction time `t`, for a vehicle on trip `T` at stop sequence `k`, predict delay at stop `k+j` whose scheduled arrival is approximately `t + h`.
- **Label:** observed delay from `silver.stop_arrivals` (Phase 5), never the agency's predicted delay.
- **Baselines (scored on identical rows):**
  1. Schedule only (predict 0 delay)
  2. Persistence (current delay propagates unchanged)
  3. **Agency prediction:** latest KCM trip-update prediction for that stop as of time `t` (from snapshots; strict as-of join, no future snapshots)
- Features (all point-in-time correct, computed as of `t`): current delay, delay trend over last N stops, scheduled remaining runtime, historical segment runtime stats for hour band (computed from training window only), route, direction, hour, day type, headway to vehicle ahead, weather now, event flag, WSDOT congestion near remaining segments, active alerts on route.
- Model: LightGBM regressor per horizon or horizon as a feature.
- **Evaluation:** time-based split (train on weeks 1..n-1, test on week n), rolling backtest across several weeks. Metrics: MAE and p90 absolute error by horizon, route type, and condition (rain, event, normal). Report where the model wins and loses vs the agency.
- MLflow: log params, metrics, data window, feature list, git SHA. Register to UC as `workspace.transit_ml.delay_model`.
- Promotion gate (`ml/evaluate.py`): challenger and champion scored on the same latest test week; promote (`@champion` alias) only if MAE improves by at least a set margin and no horizon regresses beyond tolerance.
- `train_job` weekly; scoring inside `ingest_job` (batch). Model Serving endpoint optional for demo only.
- Site page: Model scorecard (error by horizon vs each baseline, over time).

**Manual verification:**
- [ ] Leakage audit: for 10 random training rows, confirm every feature timestamp is earlier than `t`.
- [ ] Baseline sanity: persistence beats schedule-only at short horizons (if not, something is wrong).
- [ ] Rerun training with the same data window: metrics reproduce within noise.
- [ ] Promotion gate test: deliberately train a worse model (drop key features) and confirm it is NOT promoted.

**Exit:** scorecard live with honest comparison against the agency baseline.

---

### Phase 8: Engineering hardening (ongoing from Phase 1; dedicated pass here, 3 to 5 days)

**Build:**
- CI (`pr.yml`): ruff, mypy, pytest (unit), worker tests, `databricks bundle validate -t prod`, site build.
- CD (`deploy.yml`), on merge to main:
  1. build wheel
  2. `databricks bundle deploy -t prod`
  3. run an integration smoke test (small run of `ingest_job` on a fixed fixture prefix, assert tables updated)
  4. `wrangler deploy` for workers
  5. Pages deploys automatically from git
- Data quality expectations as code with thresholds; failing critical checks fail the job and alert.
- `docs/runbook.md`: feed silent, Databricks quota exceeded, schedule version mismatch, token expired, site stale, how to backfill after outage.
- Token rotation reminder (token expiry date in runbook).
- Cost/usage notes: compute minutes per day by job.
- ADRs for every major choice (push collectors, no Lakeflow pipelines, file-based exports, arrival inference method, metric definitions, promotion gate).

**Manual verification:**
- [ ] Open a PR with a failing test: CI blocks merge.
- [ ] Merge a trivial change: prod redeploys automatically, jobs keep running.
- [ ] Simulate outage: disable collector for 2 hours, re-enable, confirm ingest catches up and feed_health shows the gap.

**Exit:** a stranger could clone the repo, follow README + this setup section, and reproduce the system.

---

### Phase 9: Planner brief agent, polish, user feedback (1 week+)

**Build:**
- **Weekly planner brief:** a job queries gold tables, builds a compact structured summary, and calls an LLM (Databricks foundation model endpoint if available on Free Edition, otherwise an external API key stored as a secret) to write a one-page brief: top problems by rider-minutes, week-over-week changes, likely drivers, top schedule recommendations, each claim linked to the underlying numbers. Store briefs in `gold.weekly_briefs`, publish on site.
- **Brief evaluation:** automated check that every number in the brief matches the source table (numeric grounding), plus a small manual rubric. This is the "agent with evals" story.
- **Methodology page:** data sources, metric definitions, arrival inference method and validation results, attribution caveats, known limitations.
- **Before/after analysis:** if a Metro service change happened during collection, compare reliability on affected routes before vs after.
- Stretch: equity lens (ACS), bridge-opening detection from simultaneous stalls near Ship Canal bridges, route watch digest.
- **User feedback:** share with at least one real stakeholder (Metro planning contact, Seattle Transit Blog ridership writers, transit advocates). Record feedback and resulting changes in `docs/feedback.md`.
- Final write-ups: engineering README, one-page customer brief (`docs/brief.md`), short demo video.

**Exit:** project is portfolio-ready; feedback documented.

---

## 9. Metric definitions (initial; finalize in `docs/metrics.md`)

| Metric | Definition |
|---|---|
| Delay | observed arrival minus scheduled arrival, seconds |
| On-time | -60s <= delay <= +300s (confirm Metro's official window) |
| OTP | on-time stop visits / stop visits with observed arrival |
| Headway (actual) | time between consecutive vehicles on same route/direction at a stop |
| Bunching | actual headway < 25% of scheduled headway |
| Trip delivery rate | scheduled trips with any realtime observation / scheduled trips |
| Rider-minutes lost | sum over stop visits of max(delay, 0) x estimated boardings affected |
| Feed freshness | now minus latest feed header timestamp ingested |

---

## 10. SLOs (targets to report on the health page)

| SLO | Target |
|---|---|
| Collector uptime | > 99% of minutes with a successful upload |
| Data freshness on site | < 60 min (p95) |
| Ingest job success rate | > 98% of scheduled runs |
| Trip match rate | > 95% |
| Arrival inference coverage | > 85% of scheduled stop visits |
| Compute | stay under Free Edition daily quota with headroom |

---

## 11. Risks and fallbacks

| Risk | Fallback |
|---|---|
| Files API upload from Worker fails | GitHub Actions collector (5+ min cadence, less reliable) or always-on local/free VM with Databricks CLI |
| Daily quota exceeded | Increase ingest interval to 60 min; collectors keep buffering |
| KCM trip IDs mismatch schedule after service change | Load schedule by feed date; match against the version active on that service date |
| Arrival inference unreliable on some routes | Report coverage honestly; restrict ML to routes with good coverage |
| Workspace deleted | Rebuild from `sql/setup.sql` + bundle deploy; raw data loss is the real risk, so periodically export compressed silver snapshots to free external storage (e.g. Cloudflare R2) |
| Worker free tier limits (CPU, KV writes) | Keep Worker logic to fetch+upload only; KV only for publisher |
| Feed format/URL change | Feed health alert fires; update config; document in runbook |

---

## 12. Working agreements (for you and the coding agent)

- Small PRs, one concern each. Every PR includes tests for new logic.
- Business logic lives in `src/transit_reliability/` as testable functions; job entrypoints stay thin.
- No secrets in code, notebooks, or logs.
- Every non-obvious decision gets a short ADR in `docs/decisions/`.
- Every phase ends with the manual verification checklist completed and results recorded (numbers, not just checkmarks).
- Measure before optimizing; record compute usage after each phase.
- When a check fails, write down what was found before fixing it. These notes become interview material.

---

## 13. Rough timeline

| Weeks | Phases |
|---|---|
| Week 1 | Setup, Phase 1 (collector running ASAP), Phase 2 |
| Week 2 | Phase 3, Phase 4 (public thin slice) |
| Weeks 3-4 | Phase 5 (arrivals + metrics) |
| Weeks 5-6 | Phase 6 (context, attribution, recommendations) |
| Weeks 6-7 | Phase 7 (ML, once 3+ weeks of data exist) |
| Week 8 | Phase 8 hardening pass, Phase 9 |

Collection runs continuously from Phase 1 onward, so by Phase 7 there is enough history to train and backtest.
