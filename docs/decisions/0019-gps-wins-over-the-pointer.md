# 0019: GPS geometry wins over the next-stop pointer; evidence never collapses an interval

Date: 2026-10-20
Status: accepted and applied (sandbox verified on 2026-10-17; prod deployed and rebuilt from 2026-10-13 on 2026-10-20)

## Context

The proof report (decision 0016) showed arrivals "known to the second": 9.5 percent of measured
arrivals on 2026-10-17 had `arr_lo = arr_hi`. The first diagnosis (the pointer runs ahead of the bus)
was only part of it. Measured in a sandbox on 2026-10-17:

- the feed's next-stop pointer moves while the bus is still short of the stop: 27,568 of 282,059
  measured arrivals (9.8 percent); median 76 ft short, 19,371 within 100 ft, 473 more than 2,000 ft;
- the dominant cause of zero width: the arrival was capped at the first dwell or pointer report, and
  then the lower bound was set to the same instant (`a_lo = min(a_lo, a_hi)`). A STOPPED_AT report with
  the pointer on the next stop usually is the same report as the pointer's first advance, so the
  interval became a point (25,649 of 25,800).

## Decision

1. The pointer may narrow the GPS interval, never contradict it. If it moved at or before the GPS
   lower bound it is ignored for the arrival cap. How far short of the stop the bus was is stored in
   the new column `stop_visits.pointer_lead_ft` (NULL when the pointer was used or not in conflict).
2. When evidence (a dwell report) puts the arrival at or before the GPS lower bound, the interval is
   widened down to the previous fix and the estimate is the midpoint, instead of collapsing to the
   evidence's instant.
3. `zero_width_arrival_interval_share` is now an enforced daily check (maximum 1 percent), no longer a
   warning.

## Measured effect (2026-10-17, sandbox vs prod)

Zero-width arrivals 9.47 percent to 0.01 percent; median interval width 25 s to 26 s, 90th percentile
40 s to 41 s; no invariant violations. For the 27,659 arrivals that changed, the estimate moves by a
median of 9 s earlier (10th to 90th percentile: -18 s to +9 s); 98 percent got a wider interval. On the
common lens, timepoint arrivals: early 4,614 to 4,571, on time 26,869 to 26,602, late 6,745 to 6,625,
uncertain 2,902 to 3,332. Uncertainty rises because it is no longer hidden.

## Lessons

- Evidence that conflicts with a measurement should be recorded, not silently resolved; the lead in
  feet turns each conflict into data about the agency's feed (it appears to advance the pointer as the
  bus approaches, a median 76 ft short).
- A diagnosis from one example is a hypothesis. The first fix addressed 1,732 conflicts and moved the
  zero-width share from 9.47 to 9.15 percent; only measuring the remaining rows found the real cause.
- Two evidence sources that usually coincide (dwell report and pointer advance) hide each other's
  effects when attributing a cause by "which value equals the result".

## Prod result (2026-10-20)

Zero-width arrivals about 0.01 percent on every rebuilt day (was 9.5 percent); the pointer is ignored on
about 10 percent of arrivals every day with a median lead of 76 to 79 ft, so the agency advances its
pointer as the bus approaches the stop; independent audit 25 passed, 0 failed, 3 warnings; health checks
and the enforced zero-width check passed. Rebuild timings: stop visits 865 s, trip performance 101 s,
prediction errors 161 s, route performance 75 s, prediction accuracy 75 s.
