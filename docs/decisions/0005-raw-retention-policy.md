# 0005: Raw retention policy (delete only what is proven safe)

Date: 2026-10-14
Status: accepted (policy and read-only report built; no deletion automated)

## Context

Raw `.pb` files grow about 1.8 GB per day (trip updates are most of it). Databricks Free
Edition publishes no storage quota, and what happens when the volume is full is not
verified; the likely outcome is that uploads are rejected, which would lose the minutes
the Worker could not write. But raw files are also the only copy of what KCM published
(feeds overwrite themselves), and bronze keeps only the fields the parser extracts.
Measured 2026-10-14: bronze trip updates are 146 MB for 36M rows, about 10x smaller than the
raw files for the same period.

## Decision

A raw file may be deleted only when all of these hold:

1. **Old enough:** its `dt=` folder is older than the retention window. Defaults:
   trip updates 30 days; vehicle positions and alerts are kept (small) until storage
   measurements say otherwise.
2. **In bronze:** its full path appears in `source_file` of the bronze table.
3. **Silver verified for its date:** the silver tables built from it passed the Phase 3
   checks. "In bronze" alone is not enough because a parser bug or an ignored field can
   only be fixed from raw.
4. **Not a manual file:** `*-smoke.pb` and anything without a numeric epoch name is never
   touched.

Deletion is a manual, two-step action: run the read-only report, read its numbers, then
delete the exact listed folders yourself. No script in this repo deletes raw files.

## Tooling

`scripts/raw_retention_report.py` (logic in `src/transit_reliability/retention.py`, tested)
groups old files into: blocked (not in bronze), waiting for silver, deletable. Without
`--silver-verified-through DATE` nothing is reported as deletable.

## Alternatives considered

- Delete as soon as bronze has the file: rejected; loses the ability to reparse.
- Keep everything: rejected as the long-term plan because storage is unbounded against an
  unknown quota; fine for now while measuring.
- Automated deletion job: deferred until silver exists and the policy has been exercised
  by hand.

## Consequences

- No deletion is possible before Phase 3 silver exists, so storage keeps growing until
  then (about 54 GB per month of raw). Track it in `docs/runbook.md`.
- Overflow protection also needs an alert on Worker upload failures (Phase 8).
- If storage forces it earlier, the least-bad lever is shortening the trip-update window
  after a manual spot-check that the silver prototype reproduces the bronze content.
