# 0022: Segment running times against the timetable (proposed)

Date: 2026-10-20
Status: accepted and built 2026-10-20 (first look in `docs/findings.md`)

## Question

Between consecutive timepoints, how long do buses really take compared with the timetable, by route
segment, time of day and day type, and where is the schedule too tight or too loose?

## Proposed design

- `gold.segment_runs` (grain: service_date, trip_id, from timepoint, to timepoint): scheduled seconds,
  observed seconds (arrival at the next minus departure here), the interval width, the Pacific hour band,
  day type (weekday, Saturday, Sunday). Built from `stop_visits`, about 55,000 rows a day, rebuilt for
  recent dates like the other gold tables.
- `gold.segment_runtimes` (grain: route, direction, from stop, to stop, band, day type; trailing
  28 complete days): n, scheduled median, observed p50 / p85 / p95, median excess, share over schedule.
  Quantiles are recomputed from `segment_runs` each day (they cannot be pooled from daily summaries).
- A flag `detour_alert_active` from the alerts feed (route named in an active alert whose text contains
  "rerouted" or "detour"), because on a detour the arrival estimates are weaker and the timetable is
  known to be wrong.
- A schedule-findings view following the design doc: where the scheduled runtime is below the observed
  p50, propose the observed p85 and estimate the effect; rank by trips affected (rider weighting waits for
  ridership data, gate G2).
- Checks: row-count recount from `stop_visits`, no negative or zero scheduled runtimes, observed within
  a plausible ratio band; public page gets a segment section and the findings.

## Decisions taken while building

- Public page: aggregates only (typical running time against the timetable by time of day, the share of
  runs slower than scheduled, and how many segments fit the timetable). Route-level results stay in
  `segment_runtimes` and the findings, because they are estimates with an interval and need the agency's
  own explanation beside them (the owner did not ask for named routes to be published; revisit on request).
- The detour flag is a route-level, whole-day text match on reroute or detour alerts. It matched an
  independent Python evaluation exactly on 2026-10-16 (24 routes, 23 percent of runs), and flags route 77.
- Observed running time uses a departure and an arrival estimate, each with an interval; the per-run
  uncertainty is kept in `obs_width_s` and summaries report its median.
- A schedule-version fix made in the same change: `expand_schedule` now re-expands the current service
  date if a newer feed version is chosen for it (`dates_to_expand`), because earlier dates keep the timetable
  that applied when they were built.
