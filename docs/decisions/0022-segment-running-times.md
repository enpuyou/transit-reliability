# 0022: Segment running times against the timetable (proposed)

Date: 2026-10-20
Status: proposed (first look done, see `docs/findings.md`; awaiting the owner's go-ahead to build)

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

## Not decided

Whether to publish segment names publicly as "schedule problems" (they are estimates with an interval;
Metro's own explanation, such as a detour, should be shown beside them).
