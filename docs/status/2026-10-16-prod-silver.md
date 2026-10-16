# Project Status: Phase 2 closed, Phase 3 silver in prod

Date: 2026-10-16 (data through about 2026-10-16 01:00 UTC)
Branch: `main`; commits are local, nothing pushed since `7082b6d`.

## Summary

Collection has run since 2026-10-13 21:43 UTC. Bronze ingest and the silver job both run in
prod on schedules. Silver holds the schedule, schedule-matched vehicle positions with a
quarantine table, and trip-update snapshots (runs of unchanged predictions). Everything was
verified against independent SQL, not just job status.

## Phase 2 exit (closed)

| Requirement | Result |
|---|---|
| Prod ingest on schedule for 24 hours | 57 runs, all `SUCCESS`, 0 failed; routine runs 96 to 264 s (median 173). One planned 63 minute gap (bronze rebuild). |
| No duplicate file processing | 0 files with more than one `ingested_at` in any bronze table |
| Compute usage measured | 2026-10-15: 21.5 DBU jobs, 10.7 DBU SQL (heavy day); quota still unpublished (`docs/runbook.md`) |

## Phase 3 status (design doc exit: match rate, quarantine rate, daily static refresh)

| Check | Target | Result (prod, 2026-10-16) |
|---|---|---|
| Trip match rate | above 95% | **100%** on 2026-10-13, 14, 15; none unknown |
| Quarantine rate | about 2% or less | **1.6%** (55,176 of the bronze rows: 47,227 no timestamp, 7,949 zero coordinates) |
| Static schedule refresh daily | daily | GitHub workflow daily 11:17 UTC; silver job daily 11:45 UTC; first scheduled silver run is 2026-10-16 11:45, not yet observed |
| After-midnight trip check | correct timestamp | verified in dev: GTFS `29:30:00` on 10-14 is 05:30 Pacific on 10-15 |
| Spot-check a known route | plausible track | route 100001, bus 4353, trip 562518701: ordered stops 2 to 18, layover then moving |

Silver tables in prod: `gtfs_*` (7), `gtfs_feeds`, `scheduled_trips` (through tomorrow),
`scheduled_stop_times`, `vehicle_positions` (2.97M fixes), `quarantine_vehicle_positions`,
`trip_update_snapshots` (25.6M runs from 76.6M snapshots). Not built yet: `alert_versions`.

## What happened in the deploy pass (details in decisions 0009 and 0010, runbook)

- Deployed the trimmed sync, the new parser column, and the silver job.
- Rebuilt prod bronze from raw so `trip_schedule_relationship` covers all history. The rebuilt
  tables matched the old totals exactly up to the same timestamp.
- First prod silver run matched independent SQL on snapshots but differed by one run. Cause:
  a prediction silent for 86 minutes was split or merged depending on batching. Fixed with an
  explicit one-hour silence rule, then the table was rebuilt and re-verified exactly.
- Found along the way: 99 canceled trips so far (about 40 a day) that bronze used to hide;
  silver trip snapshots are fewer rows but not fewer bytes than bronze; cancellation explains
  only 20 of 135 trips announced but never seen on 2026-10-14.

## Open items

- Observe the first scheduled silver run (2026-10-16 11:45 UTC) and the second GTFS workflow run.
- Cleanup not yet done: stale `dev_transit_bronze.*` and `dev_transit_silver.*` tables, and the
  three orphaned `bronze_*` checkpoints. Destructive; needs your run (the permission classifier
  blocks deletions for the assistant).
- Enhanced alerts (creation and modification times, lifecycle, severity) are not collected.
  Plan: a GitHub workflow like the GTFS one (the Worker has no CPU headroom).
- The Worker's CPU (9 to 16 ms against a 10 ms limit) and two unexplained five-minute
  outages (2026-10-14 02:45 and 2026-10-15 17:01 UTC): check the Cloudflare dashboard logs for
  `exceededCpu`.
- Token for the GTFS workflow: confirm its real expiry (`docs/runbook.md`).
- Gates from decision 0008: G3 partly done (terms read). G1 revised: the ride test is optional; accuracy
  is bounded by an error budget measured from data (decision 0011).

## Added after this note was first written (2026-10-16)

- `stop_visits` (silver) and `trip_performance` (gold) are in prod; see decisions 0011 and 0012 and
  `docs/gold_tables.md`. Accuracy of stop times is bounded by an error budget measured from data
  (the ride test was waived). On-time is judged under three lenses with intervals; the owner chooses
  which to headline.
- Headline lens chosen by the owner: `common` (1 minute early to 5 minutes late).

- `prediction_errors` (gold) is in prod (decision 0014). First result, one day: the agency's
  predictions are far better than the schedule but no better than "the bus's current delay carries
  on" at 15 and 30 minutes. Caveat: the comparison favors persistence somewhat (shared inference).

- `route_performance` and `prediction_accuracy` roll-ups are in prod, and `docs/findings.md` is the
  dated log of claims with evidence and caveats. The silver job has 10 tasks; the first scheduled
  (unattended) run is 2026-10-16 11:45 UTC.

## Next

1. Stop visits: join observed positions to the schedule and infer arrival times as intervals
   with a method label, using position along the route shape (the stop-sequence field is not
   literal). This is where gate G1 (ride test) is decided.
2. `alert_versions` from the enhanced alert feed.
3. Then delay, prediction error by horizon, and the first reliability measures.
