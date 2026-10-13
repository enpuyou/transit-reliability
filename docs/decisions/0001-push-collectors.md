# 0001: Push-based external collectors

Date: 2026-10-13
Status: accepted

## Context

Databricks Free Edition restricts outbound internet access to trusted domains and
enforces a daily compute quota. The Files API accepts inbound uploads to Unity Catalog
volumes (verified in `0000-setup-notes.md`). No public archive of KCM realtime data
exists, so training data only exists from the day collection starts.

## Decision

- A Cloudflare Worker (`workers/collector`) runs on a one-minute cron, fetches the KCM
  GTFS-Realtime feeds, and PUTs the raw bytes to
  `/Volumes/workspace/transit_landing/raw/<source>/dt=<UTC date>/<epoch_ms>.pb`.
- Vehicle positions and trip updates are fetched every run; alerts every fifth minute.
- Files are immutable and never deduplicated by the Worker (KV free-tier writes are too
  limited). Bronze/silver dedupe on the feed header timestamp.
- A failure on one source is retried once and never stops the others.
- Collection continues when Databricks is down or over quota only to the extent the
  Files API is reachable; there is no local buffer in v1. Gaps are surfaced by the
  file-count and gap checks in `docs/commands.md`.

## Consequences

- Collection is decoupled from compute spend; ingest catches up on its own schedule.
- Credentials live as Worker secrets (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`).
- A silent upstream outage shows up only as missing files, so a freshness alert is
  required (Phase 8).
