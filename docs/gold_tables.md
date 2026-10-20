# Gold tables

Gold holds the numbers people read. Each is built from silver, states its grain in one sentence, and
carries the counts behind every figure.

## `trip_performance` (built)

**One row =** one trip on one service date (about 13K a day). Table: `transit_gold.trip_performance`.
Design and verification: decision 0012.

| Column group | Columns | Meaning |
|---|---|---|
| Identity | `service_date`, `trip_id`, `route_id`, `route_short_name`, `direction_id`, `trip_headsign`, `block_id` | which trip |
| Schedule | `scheduled_start`, `scheduled_end` | first departure and last arrival as scheduled |
| Status | `trip_status` | `observed`, `not_observed` (no fixes at all), or `canceled` (Metro marked it) |
| Coverage | `n_stops`, `n_stops_measured`, `n_timepoints`, `n_timepoints_measured` | how much of the trip we could time |
| Start | `first_stop_dep_delay_s`, `first_stop_dep_method` | departure from the first stop against schedule; NULL if the bus was already past it when first seen |
| Delay at timepoints | `median_delay_s`, `p90_delay_s`, `max_delay_s`, `min_delay_s` | seconds late (negative is early) |
| Along the trip | `first_tp_delay_s`, `last_tp_delay_s`, `delay_growth_s` | whether delay built up or was recovered |
| Early departures | `n_tp_departures_measured`, `n_tp_left_early` | timepoints where the bus definitely left more than 1 minute early |
| Lenses | `n_<lens>_early`, `_on_time`, `_late`, `_uncertain` for `strict`, `common`, `metro_like` | timepoint arrivals by category; the four add up to the timepoints that have an interval |
| Rider rule (headline) and departure view | `n_rider_*`, `n_dep_*` (each `_early`, `_on_time`, `_late`, `_uncertain`) | timepoints scored by the rider rule (last stop excluded; an arrival or departure bound is enough to score) and by departure alone |

**Headline: the rider rule** (decision 0021, chosen 2026-10-20), counts `n_rider_early`, `_on_time`,
`_late`, `_uncertain`. It uses the `common` window (1 minute early to 5 minutes late) but judges each
side where it matters to a rider: **early on the departure** (a bus that leaves early is a missed bus;
arriving early and holding is normal), **late on the arrival** (at the first stop, where the bus is
parked, the departure), **on time** when certainly neither. The last stop is not scored. Every bound is
evidence on its own: a departure with only a lower bound (the bus was last seen still at the stop) can
prove lateness at the first stop and "not early" anywhere, never "on time" alone. `n_dep_*` is the
departure-only view of the same window. The arrival-only lenses (`strict`, `common`, `metro_like`) stay
beside it, never instead. The pure rule is `classify_rider` in `reliability/lenses.py`; the SQL twin is
`rider_class_sql`; the audit recounts it independently from `stop_visits`.

**Reading it.** Delays are estimates from GPS (typically within about 15 s). A timepoint is counted
on time only if its whole interval fits the window, so the `uncertain` count is the honest slice we
cannot call. An on-time share is best given as a range: `on_time / total` to `(on_time + uncertain) /
total`, with `total` shown.

**Compare a route's trips on a day:**

```sql
SELECT date_format(from_utc_timestamp(scheduled_start, 'America/Los_Angeles'), 'HH:mm') AS start_pt,
       round(first_stop_dep_delay_s) AS start_delay_s, round(median_delay_s) AS median_s,
       round(max_delay_s) AS max_s, round(delay_growth_s) AS growth_s,
       concat(n_timepoints_measured, '/', n_timepoints) AS measured,
       n_strict_on_time, n_strict_uncertain, n_strict_late, n_strict_early, n_tp_left_early
FROM workspace.transit_gold.trip_performance
WHERE service_date = DATE'2026-10-14' AND route_short_name = '40' AND direction_id = 0
  AND trip_status = 'observed'
ORDER BY scheduled_start;
```

**Example (route 40, direction 0, 2026-10-14, timepoints, preliminary):** trips differ widely: the
07:00 trip ran close to schedule (median 33 s early), the 07:10 trip started 11 minutes late
(668 s) and stayed late (median 878 s), and the 09:06 trip ran early at every timepoint (median 4.5
minutes early; left early at 5). Worth checking trip assignment before reading the last as early
service.

## `prediction_errors` (built)

**One row =** one observed stop visit and one horizon (5, 15, or 30 minutes) where the agency's
prediction, the schedule baseline, and the persistence baseline are all available. Table:
`transit_gold.prediction_errors`; rules, verification, and first results: decision 0014.

| Column | Meaning |
|---|---|
| `service_date`, `trip_id`, `route_id`, `stop_sequence`, `timepoint` | which stop visit |
| `horizon_min`, `as_of` | the horizon and the moment the prediction is taken from (actual arrival minus horizon) |
| `actual_arrival`, `width_s` | the inferred arrival and our uncertainty about it (filter `width_s <= 60` for tight ones) |
| `kcm_error_s`, `sched_error_s`, `persistence_error_s` | predicted minus actual, seconds; positive is too late |

```sql
-- accuracy by horizon for a day: median error (bias), median and 90th percentile of absolute error
SELECT horizon_min, count(*) AS n,
       round(percentile(kcm_error_s, 0.5)) AS kcm_bias, round(percentile(abs(kcm_error_s), 0.5)) AS kcm_mae,
       round(percentile(abs(kcm_error_s), 0.9)) AS kcm_p90,
       round(percentile(abs(persistence_error_s), 0.5)) AS persistence_mae,
       round(percentile(abs(sched_error_s), 0.5)) AS schedule_mae
FROM workspace.transit_gold.prediction_errors
WHERE service_date = DATE'2026-10-14' AND width_s <= 60 GROUP BY 1 ORDER BY 1;
```

## `route_performance` (built)

**One row =** one route, direction, and Pacific start hour of the trip, on one service date. Table
`transit_gold.route_performance`. It **sums the per-trip counts** of `trip_performance` (never averages
percentages), so any share is recomputed as `sum(n_common_on_time) / sum(n_common_early + n_common_on_time
+ n_common_late + n_common_uncertain)` and always comes with that denominator. Columns: `n_trips`,
`n_observed`, `n_canceled`, `n_not_observed`; `n_timepoints`, `n_timepoints_measured`; the twelve arrival lens
counts plus `n_rider_*` and `n_dep_*`; early-departure counts; `median_of_trip_median_delays_s` (a median of trip medians, not a pooled
median); first-stop departure median and count. A trip is attributed to the hour it is scheduled to
start. Checked by conservation: trips, on-time counts, and measured timepoints equal the per-trip totals
for every date.

**A share is a range.** Report `on_time / n` (definite) to `(on_time + uncertain) / n`, with `n`, and
do not compare routes until `n` is large (`docs/findings.md`, finding 4).

## `prediction_accuracy` (built)

**One row =** one route, Pacific hour of the actual arrival, and horizon (5, 15, 30 min) on one service
date: `n` and, for each of the agency, the schedule, and persistence, the median error, median and 90th
percentile absolute error, and the number within 60 s. Only rows whose actual arrival is known within
60 s. Table `transit_gold.prediction_accuracy`. Medians of medians are not valid, so for system-level
numbers query `prediction_errors` directly. Checked by conservation against `prediction_errors`.

**Not built yet:** spacing and bunching, running time against the timetable.

## `segment_runs` and `segment_runtimes` (built; decision 0022)

**`segment_runs`. One row =** one trip between two consecutive timepoints on one service date. Table
`transit_gold.segment_runs`. `sched_s` = scheduled arrival at the next timepoint minus scheduled
departure here; `obs_s` = our estimated arrival at the next minus our estimated departure here;
`obs_width_s` = the combined width of the two estimate intervals (median about 53 s); `band` (Seattle
time: am_peak 6-9, midday 9-3, pm_peak 3-7, evening 7-midnight, night) and `day_type` (weekday,
saturday, sunday); `detour_alert` = the agency published a reroute or detour alert naming this route that
day (headline text match, whole route, whole day; it marks weaker estimates and a timetable known to be
wrong, not a measurement of the detour). A pair is kept only when it has a departure, an arrival and a
positive scheduled runtime. About 54,000 rows a day.

**`segment_runtimes`. One row =** one route segment (direction, from and to stop) in one band and day
type over the last 28 complete service dates, with at least 10 runs: `n`, `n_days`, `sched_median_s`,
`obs_p50_s`, `obs_p85_s`, `obs_p95_s`, `excess_p50_s` (median observed minus scheduled), `share_over_schedule`,
`median_uncertainty_s`, `detour_share`. Quantiles are recomputed from the runs, never pooled from daily
numbers. Read it as: positive `excess_p50_s` means the timetable is tighter than reality. A segment with
a high `detour_share` should be read with the alert text, not as a schedule error.

Checks (audit and daily suite): run count equals an independent self-join recount, no non-positive
scheduled runtime, negative observed runtimes under 0.1 percent, median observed/scheduled between 0.6 and
1.4 (0.94 measured), and the summary is current.
