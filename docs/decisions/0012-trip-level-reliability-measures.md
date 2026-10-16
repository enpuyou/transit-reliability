# 0012: Reliability measures are per trip, judged under several lenses with intervals

Date: 2026-10-16
Status: accepted (built and verified; prod table `transit_gold.trip_performance`)

## Context

The first proposal was a daily table by route and hour. The owner asked for the finer view: every
trip of every route for every day, so that a route's ~20 trips on a day can be compared and the
same view applies to every route. The owner also questioned the on-time window of 1 minute early
to 5 minutes late as too lenient, and wanted the ~15 s timing error taken into account.

On the window: there is no national standard. A search found a 2018 survey (146 bus operators) in
which "no more than 1 minute early and 5 minutes late" would cover most definitions, other agencies
range from 1 early/4 late (SF Muni) to 2 early/7 late (WMATA), and a search summary of a 2019 King
County Auditor report says Metro counts 1.5 minutes early to 5.5 minutes late at timepoints. The
report itself could not be opened (404), so **Metro's current definition is unverified**. Whatever
the window, it is a policy choice, and for frequent routes riders care about the gap between buses
more than the timetable.

## Decision

- **Grain: one row per trip per service date** (`trip_performance`, gold). Route, hour, and day
  summaries are computed from these rows when needed, so the trip-level view is the base.
- **Several lenses, side by side** (`reliability/lenses.py`), not one hard-coded window:
  `strict` (-60 s to +180 s), `common` (-60 s to +300 s), `metro_like` (-90 s to +330 s, marked
  unverified). Adding or tuning a lens is a one-line change. **Headline lens: `common`**, chosen by
  the owner on 2026-10-16 (`HEADLINE_LENS` in `lenses.py`); the others are shown alongside.
- **Intervals, not points.** Each timepoint arrival has an interval [lo, hi] relative to the
  schedule. It counts as `on_time` only if the whole interval is inside the window, `early` or
  `late` only if the whole interval is outside on that side, and otherwise `uncertain`. The four
  counts are stored per lens next to the number of timepoints, so a share can be given as a range
  (definite, definite plus uncertain) and its sample size is always visible.
- Measures per trip: scheduled start and end; trip status (`observed`, `not_observed`, `canceled`
  from the trip-level cancellation flag); first-stop departure delay and its method; median, 90th
  percentile, maximum, and minimum arrival delay at timepoints; delay at the first and last measured
  timepoint and the growth between them; timepoints measured of total; timepoint departures
  measured and how many definitely left more than a minute early; the lens counts.
- **Early departures are counted separately** (`n_tp_left_early`): leaving a timepoint more than a
  minute early strands waiting riders, and a combined "off schedule" figure would hide it.
- Not yet built (agreed as good): spacing and bunching on frequent routes; running time against the
  timetable; prediction accuracy by horizon.

## Verification

Built first on a sandbox slice of prod (294 trips: 250 random plus every canceled trip on
2026-10-14). A pure-Python reference computed from the same stop visits (using `lenses.classify`)
matched all 6,174 compared values with zero mismatches: counts per lens, that the four categories add
up to the timepoints with an interval, medians, first and last delays, early-departure counts, and
trip status (240 observed, 46 canceled, 8 not observed). Then built in prod for all three service
dates (57 s).

## Consequences and caveats

- A trip with `trip_status = 'not_observed'` has no punctuality data; counting them is service
  delivery. 2026-10-13 and 2026-10-15 show many because collection started mid-afternoon on the
  13th and the 15th had not finished when it was built; the last two service dates are rebuilt each
  run.
- The underlying times are estimates (typically within about 15 s, decision 0011). A trip showing
  large early running (for example route 40's 09:06 trip, median 4.5 minutes early) should be
  checked against the possibility that the bus was assigned to the wrong trip before it is read as
  early service.
- Percentages derived from this table must be shown with their counts.
