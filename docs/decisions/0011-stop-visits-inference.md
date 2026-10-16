# 0011: Infer stop arrivals and departures from route geometry; use the feed's stop pointer as evidence

Date: 2026-10-16
Status: accepted for building; accuracy is NOT yet validated against ground truth (gate G1)

## Context

Every reliability measure needs the time a bus actually reached and left each stop. The feed does
not provide it: vehicle positions are GPS fixes about every 20 seconds of our time. The design
doc proposed projecting fixes onto the route shape. Checking the data first (prod, 2026-10-14)
changed two assumptions:

- **`shape_dist_traveled` is in feet**, not meters (ratio to geometric length 3.28 on 60 shapes),
  and is present for every stop time and shape point.
- **`current_stop_sequence` and `stop_id` are the NEXT stop to be served, also when the status is
  `STOPPED_AT`.** 99.3 percent of STOPPED_AT fixes sit at the stop before the one named, and a
  bus waiting at the first stop for 25 minutes reports the second stop. The earlier plan to read
  STOPPED_AT as "at this stop" was wrong; using it that way put 133 of 200 trips' estimates out
  of order and produced a 90-second discrepancy against the geometry.

## Decision

- Pure inference in `arrivals/infer.py`, tested on synthetic trajectories (steady travel, layover,
  gaps, jitter, off-route fixes, backward steps, out-and-back routes with and without a lane
  offset, left and right censoring, evidence-only cases). Method: project each fix onto the
  trip's shape (window-limited around the previous position; forward-only), interpolate when
  the distance crosses `stop - 40 ft` (arrival) and `stop + 60 ft` (departure), report an
  interval [lo, hi] plus an estimate and a method label. A bracket wider than 180 s is labelled
  `wide_gap`.
- Evidence from the feed: `advance_ts` (first fix whose next stop is past this one) and dwell
  reports (STOPPED_AT fixes within 200 ft short or 150 ft past a stop that name the following
  stop). Dwell caps the arrival and floors the departure. The pointer alone supplies an interval
  (`next_stop_advance`) when geometry cannot. An ordering pass keeps estimates non-decreasing
  along a trip, widening an interval rather than inventing an impossible one.
- Silver table `stop_visits`, grain (service_date, trip_id, stop_sequence): **one row for every
  scheduled stop**, including trips never observed (method `no_fixes`), so missing service is
  countable. Columns: scheduled times, arrival and departure estimate with interval and method,
  both delays, the pointer and dwell evidence, and `n_fixes`. Built per service date by a
  cogroup of trips with their route shape; the last two service dates are rebuilt each run.

## Validation so far (not ground truth)

200 real trips (prod, 2026-10-14, 6,896 stop visits): 82 percent get an arrival estimate; bracket
width median 24 s. The feed's own stop pointer advances a median of 4 s after the geometric
arrival (p10 0 s, p90 32 s) and Metro drops a stop's prediction a median of 65 s after the
estimated departure (p10 30 s, p90 122 s, which fits about 60 s of sampling plus feed lag). Two
independent signals agreeing is supporting evidence, not accuracy. The Spark job's output equals
the pure code on all 6,896 of those stop visits. Prod, three service dates: rows equal scheduled
stop visits exactly; invariants hold with zero violations (estimate inside its interval, departure
not before arrival, stop order).

## Full-day coverage (prod, 2026-10-14, 449,085 scheduled stop visits)

Arrival estimate available for 86.0 percent (interpolated 83.8, wide_gap 1.4, next_stop_advance
0.8). Not available: not_reached 8.6 (usually the last stops; the bus leaves the feed), before_first_fix
4.0 (passed before it was first seen), no_fixes 1.4. 12,480 of 12,939 trips have at least one
estimate. At first stops, a departure estimate exists for 74.0 percent of trips; 22.6 percent
were already past the first stop when first seen.

## Found along the way

A source-side degradation: between about 17:03 and 17:30 UTC on 2026-10-14 the feed reported
about 25 vehicles instead of about 600. Our collector landed every file; their median size fell
from about 52 KB to 27 KB. Coverage for that Pacific hour was 58.8 percent against 84 to 90
percent elsewhere. Not a pipeline defect, and a good case for a feed-health check against the
schedule.

## Consequences and open items

- Gate G1 is open. The ride test (`docs/ride_test.md`) is the validation; until it is done these
  times must not be presented as accurate, only as consistent with two other signals.
- Preliminary figures exist (timepoint arrivals median 107 s late, 69 percent within the
  -1/+5 minute window; first-stop departures median 60 s late, 85 percent within the window) and
  carry selection bias (74 percent measurable) and the caveats above. They are sanity checks that
  the results are believable, not findings.
- The arrival threshold (40 ft short of the stop point) and tolerances are tunable constants;
  they should be tuned against the ride test, not by eye.
- Not handled: a vehicle swap mid-trip (all fixes of a trip are pooled), routes with
  genuinely overlapping shapes beyond the out-and-back handling, and departures at trip ends.
