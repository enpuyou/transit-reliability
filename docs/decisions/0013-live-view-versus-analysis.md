# 0013: Keep the live view and the analysis on separate paths (proposed)

Date: 2026-10-16
Status: proposed (owner to confirm); nothing built for the live path

## Context

The owner asked whether Databricks is the right place to serve a near real time display, noting that
data takes time to travel from bronze to gold, and whether the medallion layers are only for
analysis. Facts from this project:

- Metro refreshes the realtime files about every 21 seconds. The feed already contains each trip's
  current delay (for trips with an assigned bus) and the vehicles' positions.
- Our lag is a set of choices, not a property of the layers: ingest every 30 minutes, silver and gold
  once a day. They follow Free Edition limits (serverless compute only, a daily compute quota whose
  size is unpublished, only run-once streaming triggers; `docs/limits.md`).
- There is one SQL warehouse (2X-Small). CLAUDE.md forbids querying it from a public path.
- Silver and gold add what the raw feed cannot give: history, cleaning, the schedule join, and
  inferred stop times. None of that is needed to draw "where is the bus now".

## Decision (proposed)

Two paths with different latency targets, chosen per consumer rather than one pipeline for all:

1. **Live ("now"), seconds of lag, no Databricks in the loop.** A light edge service (the existing
   Cloudflare account) serves the current vehicles, and Metro's own current delays, from the feed
   with caching. This is what a public map would read. It must show the required attribution
   legend (`docs/data_reports/kcm_realtime.md`) and is a separate, small build.
2. **Analysis, minutes to a day of lag, on Databricks.** Bronze, silver and gold as built. This path
   answers history, comparison, prediction accuracy, and later models. A tool for it can connect to
   Databricks directly (owner-only or a handful of users), consistent with decision 0008.
3. **Bridge, optional.** Small gold snapshots (for example route performance for the last week) can be
   written to the edge as files after each pipeline run, for "recent performance" cards beside the
   live map. The warehouse is never the live data source.

## Alternatives considered

- Everything through Databricks with continuous streaming: possible on paid compute, but costly
  and unnecessary here, and not available on Free Edition (run-once triggers only).
- Shorten the batch intervals (for example ingest every 5 minutes): cheap to try, costs compute
  against an unknown quota, and still leaves minutes of lag; worth measuring, not a fix.
- Compute our own live delay from stop times: no; the feed already gives a current delay, and our
  inference needs the bus to have passed a stop.

## Consequences

- Freshness is a per-consumer number to state in the docs, not a global one.
- `stop_visits` and `trip_performance` recompute the last two service dates each run; that is fine
  daily and would need to become incremental before running much more often.
- The original design's `live_vehicles` gold table is replaced by the live path; `feed_health` and
  `route_status` remain candidates for the analysis path.
