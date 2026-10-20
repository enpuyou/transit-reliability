# 0021: First and last stop coverage; judging on-time by departure (proposed)

Date: 2026-10-20
Status: proposed (measurement done on 2026-10-16; owner decision pending)

## Question

Timepoint coverage is about 66 percent (decision 0016): first stop 0 percent, last stop 4.4 percent.
Would matching GPS fixes by vehicle and block (instead of by trip id) recover them? And should on-time
be judged on arrival or departure?

## Measurement (service date 2026-10-16, 12,468 observed trip-vehicle pairs, read-only SQL)

- Unassigned positions do not exist in our feed: quarantine has no MISSING_TRIP_ID rows. A bus with no
  trip is simply absent. Any fix of a bus before or after a trip is under another trip id.
- First stop: it is a timepoint on all 12,938 trips. Arrival is `before_first_fix` for 12,430 (the bus
  is already there when first seen) and 0 percent measured, structurally: the bus is parked at the
  terminal. Departure is measured for 75.3 percent (9,708 interpolated, 38 wide_gap); 2,681 (20.7
  percent) are `before_first_fix` (first seen after it left) and 488 (3.8 percent) never seen. 47
  percent of trips are first seen after the scheduled start (median 10 s before it). Earlier fixes of
  the same bus within 500 ft of the first stop exist for only 26.7 percent.
- Last stop: arrival `not_reached` for 11,859 of 12,938 (91.7 percent). The trip's last fix is a median
  1,077 ft short of the terminal stop; the bus then reappears under the next trip a median 528 s later
  (90th percentile 1,546 s; only 13 percent within 60 s), 65.7 percent of the time within 500 ft of the
  terminal. So matching by vehicle would give arrival intervals about 9 minutes wide: coverage up, but
  mostly "uncertain" on-time classes.
- Departure versus arrival coverage over all timepoints: 76.5 vs 66.3 percent. By position: first 75.3
  (departure) vs 0.0 (arrival); middle 93.2 vs 93.9; last stop has no departure. Median interval width
  at timepoints: arrival 27 s, departure 28 s. Departure at every timepoint except the last stop covers
  about 90 percent of them.

## Conclusions

1. Vehicle-and-block matching is not worth building: it adds wide intervals at the last stop and little
   at the first (26.7 percent).
2. Judge on-time by DEPARTURE at timepoints (the rider's view: a bus leaving early is a missed bus; a
   bus arriving early and holding is normal). The first stop becomes measurable (75 percent).
3. The last stop has no departure; options are to leave it out of the on-time share and say so, or to
   add an extrapolated arrival from the final fixes (distance to the stop over recent speed) with an
   explicit method and interval. Not decided.

## Consequences if adopted

New departure-based lenses and `n_*` columns in `trip_performance`/`route_performance`, a rebuild, a
change to the headline and to the public page text ("early" currently counts arrivals), and the daily
coverage checks would measure departure coverage. Arrival-based shares stay available alongside.
