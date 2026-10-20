# 0021: First and last stop coverage; judging on-time by departure (proposed)

Date: 2026-10-20
Status: partly decided. The owner decided on 2026-10-20 that the LAST stop is left out of on-time shares (and the page says so). The owner also wants BOTH arrival and departure kept, each used where it matters: late is judged on arrival, early on departure (rule below). Implementation: pending the owner's go-ahead.

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

## Owner's refinement (2026-10-20): late by arrival, early by departure

"Arriving late means more than departing late; departing early means more than arriving early." So, per
timepoint, last stop excluded, using the existing 'common' window (1 minute early to 5 minutes late) and
the interval of each estimate:

- early: the departure interval is entirely earlier than 1 minute early;
- late: the arrival interval is entirely later than 5 minutes late (first stop: the departure, because
  the bus is parked there and its arrival is not measurable);
- on time: certainly not early AND certainly not late. Departure cannot be before arrival, so a lower
  bound of either one is a lower bound of the departure, and an upper bound of either is an upper bound
  of the arrival; whichever was measured can certify;
- uncertain: otherwise. A timepoint with no estimate at all is not counted.

Measured on three days with this rule: 2026-10-16 early 7.2, on time 72.2, late 15.3, uncertain 5.3
percent (n = 66,153); 10-14: 6.7 / 71.9 / 15.0 / 6.4 (64,882); 10-18: 6.7 / 74.8 / 12.9 / 5.5 (46,536).
Compare the current arrival-only rule on 10-16: 12.5 / 64.2 / 16.3 / 7.1 (56,953) and departure-only:
7.2 / 68.6 / 18.1 / 6.1 (65,690). The higher on-time share comes from the definition (each side is judged
only where it matters), not from better service, and the page must say so.
