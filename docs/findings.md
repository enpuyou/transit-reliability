# Findings log

Dated claims with their evidence and caveats. Nothing here is final: the data covers only a few
days (collection began 2026-10-13), times are GPS estimates (typically within about 15 s), and every
percentage should be read with its sample size. Status: **preliminary** unless stated.

## 2026-10-22 trips that did not run, and whether riders were told (gold.trip_delivery, service date 2026-10-20)

Of 12,938 scheduled trips (Tuesday): 9,992 ran, 2,504 ran with at least one stop marked skipped, 16 were
canceled late, none were canceled with notice (at least 15 minutes before the start), 322 did not run and were
never announced, and 104 were on five routes that never send live data (973, 975, Valley Shuttle,
Duvall-Monroe Shuttle, Easy Loop), so whether they ran is unknown and they are left out. On tracked routes, 338
trips (about 2.6 percent) did not run and 95 percent of those were never marked canceled; the agency marked 31
trips canceled that day, 15 of which ran anyway, and its cancellations appeared a median 8 minutes after the
trip's scheduled start. When a bus's previous trip did not run, this trip did not run either 84 percent of the
time. "Did not run" means no bus position was ever matched; a bus with its tracker off looks the same (share
unknown). First caught and corrected the same day: the untracked routes had first been counted as "not run"
(about 104 trips a weekday), and 2026-10-13 is a partial day (collection began 21:43 UTC) and is left out.

## 2026-10-22 the agency's feed drops a trip at its scheduled end (service date 2026-10-20)

For trips whose last report came more than 250 m before the final stop, 90 to 97 percent of those last reports
came within a minute of the trip's scheduled end time: the agency stops showing a trip when its scheduled
time runs out, not when the bus arrives. On-time buses go quiet at the stop; late ones are cut off while
still driving. Next-trip outcomes: after a trip whose last report was over 3 km short, the next trip was
missing 2.9 percent of the time (0.5 percent otherwise). Checked against reality on 2026-10-22: 74 of 74
upcoming trips whose bus had gone quiet short of the terminal ran (`scripts/check_departures.py`).

## 2026-10-22 placing a bus between reports (scripts/lag_estimators.py, 240 trips, 2026-10-16 and 10-20)

Reports reach the map a median 57 s after the GPS fix. The raw report is a typical 253 m from where the bus
really is (behind it 84 percent of the time); moving it forward by recent speed and the timetable's pace
("mixed, halfway") cuts that to 120 m (9 in 10 within 379 m) and is more than 200 m worse than the raw report
only 3 percent of the time.

## 2026-10-20 bunching and gaps (decision 0023; frequent service, 2026-10-13 to 10-20)

About 34,000 frequent pairs a day (buses scheduled 4 to 15 minutes apart at a timepoint); a pair is
called bunched (under 25 percent of the scheduled gap) or gapped (over twice) only when the whole
estimated interval of the actual gap says so (median width about 50 s). Audit 37 passed, 0 failed.

- Typical day: 2.6 to 2.8 percent bunched, 1.2 to 1.3 gapped, 93 to 94 percent regular, 1.8 to 3 percent
  uncertain (weekdays 10-14 to 10-16; the Sunday 10-18 was calmer: 1.3 / 0.3 / 97.6).
- Time of day, weekdays pooled: the afternoon peak (3 to 7 pm) is the worst, 3.8 percent bunched and 2.0
  percent gapped, about double the morning peak (1.7 and 0.9); midday 2.0 and 0.7; evening 2.4 and 0.9.
- Highest by route (weekday, at least 300 pairs, certain classes): E Line 10.1 percent bunched and 5.4
  gapped (4,241 pairs), route 40 5.8 and 3.5, route 168 5.5 and 2.7, route 75 4.9 and 3.4, route 60 4.8 and
  2.6. Not published by name on the site; these are estimates with an interval and need more weeks.
- The median actual-over-scheduled gap is 1.00, as it should be on average; the story is the tails.
- Ridership (gate G2) found no downloadable licensed source, so rider-weighted rankings are not possible;
  rankings use trips affected (decision 0023).

## 2026-10-20 segment running times in prod (decision 0022; 2026-10-13 to 10-19, 389,000 segment runs)

Built `segment_runs` (about 54,000 rows a day) and `segment_runtimes` (7,786 segment, band and day-type rows,
2,877 with at least 30 runs). Audit 32 passed, 0 failed; the detour flag equals an independent Python
evaluation of the alert text on 2026-10-16.

- The first-look picture holds on every day: median 14 to 26 s FASTER than the timetable; weekday medians by
  time band: morning peak -15 s (41.6 percent of runs slower than scheduled), afternoon peak -23 s (39.6),
  midday -26 s (36.9), evening -21 s (37.3), overnight -21 s (36.8). The peaks are the tightest.
- Detours are common and distort the extremes: on a typical day 7 to 23 percent of runs are on a route with
  a reroute or detour alert (43 percent on Saturday 10-17, a day of street events). Of the 25 largest weekday
  overruns (at least 30 runs), 22 are on routes with such an alert: route 77 throughout (the Roosevelt Station
  to NE 125th St segment is scheduled 540 s and observed 1,122 s in the afternoon peak; NE 130th St and 5th
  Ave NE to NE 125th St and 30th Ave NE is scheduled 240 s and observed 601 s).
- The one large overrun with NO alert: route 906, S 180th St and Andover Park W to S Carr Rd and Talbot Rd S,
  afternoon peak, scheduled 540 s, observed median 895 s (p85 1,066 s, 34 runs). Either the timetable is
  tight there or an unannounced diversion; it is the best candidate for a real schedule finding so far, and
  needs more days before it is quoted.
- Not published by name on the site (aggregates only), per decision 0022.

## 2026-10-20 running time against the timetable (first look; 3 weekdays, 2026-10-14 to 10-16)

Segment = consecutive timepoints of a trip; observed running time = arrival at the next timepoint minus
departure from this one (our estimates, each with an interval; the two intervals add to a median 53 s of
uncertainty per segment); scheduled = scheduled arrival at the next minus scheduled departure here.

- 160,819 segment trips. Scheduled median 420 s, observed 391 s: buses run a median 21 s FASTER than the
  timetable; 39 percent of segments take longer than scheduled; observed/scheduled ratio 0.69 / 0.95 /
  1.23 at the 10th / 50th / 90th percentile. The weakest padding is the morning peak (median 12 to 15 s
  under schedule, 43 percent over) and the evening peak; midday is the loosest (25 to 28 s under).
- Of 991 segments with at least 30 trips, 63 (6 percent) run at least 60 s over schedule at the median and
  175 (18 percent) at least 60 s under (padded).
- Largest overruns: route 77, e.g. Roosevelt Station Bay 3 to NE 125th St and 28th Ave NE, scheduled 540 s,
  observed median 1,130 s (PM peak, 46 trips, all over); NE 130th St and 5th Ave NE to NE 125th St and 30th
  Ave NE, scheduled 240 s, observed 617 s. Then routes 164, 150 and 240 near Kent and Eastgate (170 to 270 s
  over).
- Route 77 is real and explained by the agency's own alerts: it was rerouted off NE 130th and NE 125th
  between 1st and 15th Ave NE for construction (2026-10-09 to 10-19) and, separately, off 30th Ave NE
  between NE 125th and Lake City Way from 2026-08-29 through 2027-03-27. The timetable is not adjusted for
  the longer path. Cross-check: for six PM trips the agency's own last predicted delay at the next
  timepoint was 278 to 749 s, ours 430 to 967 s; both say 10 to 15 minutes late through the segment.
- Caveat: on a detour the bus leaves the route shape, so arrival estimates around it rest on fewer usable
  fixes; segment findings should be flagged when an active detour alert covers the route.
- Not yet built (decision 0022 proposed): per-trip segment table, trailing-window summary by time band
  and day type, and a flag from active alerts.

## 2026-10-20 the headline on-time measure is now the rider rule (decision 0021)

Owner decision: judge EARLY on the departure (a bus that leaves early is a missed bus; arriving early and
holding is normal) and LATE on the arrival (at the first stop, on the departure), last stop left out
(its trip data ends about 1,000 ft before it and the bus is then absent for a median 9 minutes). Counts
`n_rider_*` in `trip_performance` and `route_performance`; the arrival-only view stays alongside.
Prod rebuilt 2026-10-13 onward; audit 27 passed, 0 failed, 3 warnings; independent recount equal.

Per day, early / on time / late / uncertain (share of scored timepoints), rider rule vs the arrival-only
view used until now:

| Day | Rider rule (n scored) | Arrival-only (n) |
|---|---|---|
| 10-14 | 6.4 / 68.6 / 16.1 / 8.9 (68,179) | 11.7 / 63.8 / 16.0 / 8.6 (55,783) |
| 10-15 | 6.1 / 68.4 / 18.2 / 7.2 (68,764) | 11.1 / 63.7 / 17.6 / 7.6 (56,315) |
| 10-16 | 6.9 / 69.6 / 16.6 / 6.9 (68,808) | 12.5 / 64.2 / 16.3 / 7.1 (56,953) |
| 10-17 | 6.0 / 69.0 / 17.0 / 8.0 (50,242) | 11.1 / 64.7 / 16.1 / 8.1 (41,130) |
| 10-18 | 6.5 / 72.1 / 14.0 / 7.3 (48,443) | 11.7 / 67.4 / 13.5 / 7.5 (40,084) |

- Read the on-time share as a DEFINITION effect: about 5 points more on time than the arrival-only view
  because buses that arrive early and wait are no longer counted early. It does not mean service
  improved. The late share is about the same (it is judged on the arrival either way, plus the first
  stop's departure).
- The rule scores 94.4 percent of non-last timepoints (2026-10-18); the arrival-only view scored only 66
  percent of all timepoints, none at the first stop.
- 3,049 non-last timepoints on 10-16 have a departure lower bound without an estimate (the bus was last
  seen still at the stop); they count as evidence of lateness or "not early", never alone as on time.
- Earlier per-day tables below (arrival-only) remain valid as that view.

## 2026-10-20 second correction: arrivals are no longer collapsed to a single instant (decision 0019)

The proof report found that about 9.5 percent of measured arrivals had a zero-width interval. Cause:
the feed's next-stop pointer (and dwell reports) usually arrive slightly BEFORE the bus reaches the stop
(median 76 to 79 ft short, about 10 percent of arrivals every day, 2026-10-13 to 10-20), and the code
capped the arrival at that instant, then set the lower bound to the same instant. Fixed and prod rebuilt
from 2026-10-13 on 2026-10-20: zero-width share is now about 0.01 percent on every day; the pointer is
ignored when it moves at or before the GPS bound, and `stop_visits.pointer_lead_ft` records how short of
the stop the bus was.

- Common lens, all trips, per day (early / on time / late / uncertain): 10-14: 6,540 / 35,571 / 8,901 /
  4,771; 10-15: 6,258 / 35,872 / 9,912 / 4,273; 10-16: 7,094 / 36,543 / 9,277 / 4,039. For 10-16 that is
  uncertain 3,394 -> 4,039 and on time 36,960 -> 36,543 compared with the first correction (uncertain
  was 5,975 before any fix). Uncertainty rose because it is no longer hidden.
- Prediction accuracy (Finding 1) is unchanged. Pooled 10-14 to 10-16, median absolute error agency /
  persistence / schedule: 5 min 62 / 57 / 144 s, 15 min 96 / 94 / 159 s, 30 min 132 / 132 / 177 s
  (n = 947,152 / 659,535 / 298,634).
- Independent audit after the rebuild (2026-10-17): 25 passed, 0 failed, 3 warnings (the coverage
  limits listed in the entry below).

## 2026-10-19 correction after an independent audit (read this first)

An independent reviewer (a separate agent with no knowledge of how the pipeline was built) audited the
code and the live data; see decision 0016. Everything below was computed BEFORE the fix it found.

- **Bug found and fixed:** when a bus switched trips, its first GPS fix could still carry the previous
  trip's next-stop pointer, and the inference read it as "every stop already served". It affected about
  7.7 percent of interpolated arrivals (about 1,000 trips a day) with intervals up to 6,688 s, an early
  bias of about 50 s on those rows. After the fix (prod rebuilt 2026-10-19 from 2026-10-13) that share
  is 0.09 percent (measured on 2026-10-16).
- **What changed on 2026-10-16 (common lens, all trips):** early 7,887 -> 7,186, uncertain 5,975 ->
  3,394 (-43 percent), on time 34,363 -> 36,960. Median trip delay about 102 s -> 106 s.
- **Prediction accuracy (Finding 1) survives.** Pooled over 10-14 to 10-16, median absolute error,
  agency / persistence / schedule: 5 min 62 / 56 / 144 s, 15 min 96 / 94 / 159 s, 30 min 132 / 133 /
  176 s (n = 947,819 / 661,297 / 300,950 scored arrivals, up from 875,850 / 614,223 / 279,074 because
  fewer arrivals now have wide intervals). 90th percentile of absolute error: agency 170 / 258 / 354 s.
- **Read all on-time shares with these limits (found by the audit, not fixed):** the first timepoint of
  a trip is almost never measured and the last is measured 4.4 percent of the time, so only about 66
  percent of scheduled timepoints have an interval; shares describe the middle of trips. "Early" is
  judged on ARRIVAL, which is normal (buses hold for the scheduled departure); the departure-based
  figure `n_tp_left_early` is the meaningful one. Dates 2026-10-13 and 2026-10-18 are partial and show
  thousands of "not observed" trips that are missing data, not missed service. About 100 prediction
  errors a day are about a day off (the same trip id on the next day).
- The per-day numbers in the older sections below that depend on arrival times (early and uncertain
  counts, delay percentiles) are stale until re-run; the prediction medians above replace theirs.

## 2026-10-18 data-quality note
Findings below were computed from service date 2026-10-14 only, which was built by the first full run
and is unaffected by the incident in decision 0015 (silver vehicle positions dropped most fixes from
2026-10-15 on, since repaired). Dates 2026-10-15 to 2026-10-17 are now complete (arrival coverage 86.1,
86.9, 85.2 percent) and should be used to re-check each finding.

## 2026-10-18 re-check on more days (Wed to Fri pooled, plus Saturday)

- **Finding 1 holds.** Prediction accuracy at 15 minutes ahead is nearly identical on each weekday:
  agency median absolute error 96 s on 10-14, 10-15, and 10-16 (91 s on Saturday 10-17), persistence
  94 s each weekday (89 s Saturday), schedule 156 to 162 s; share within one minute 33 percent for the
  agency and 33 to 35 for persistence. Pooled over the three weekdays (n = 875,850 / 614,223 / 279,074
  arrivals at 5 / 15 / 30 minutes): agency 62 / 96 / 133 s, persistence 56 / 94 / 134 s, schedule 144 /
  158 / 176 s; 90th percentile of absolute error agency 170 / 259 / 355 s, persistence 152 / 250 / 365 s.
  So persistence is about 6 s better at 5 minutes and tied at 15 and 30.
- **Finding 3 holds.** Three weekdays pooled, definitely on time by trip start hour: 72.4 percent (5 AM),
  68.5 (6 AM), about 61 (7 AM to noon), then falling to 54.7 to 56.4 percent from 3 PM to 6 PM, with 20 to
  22 percent late in the afternoon peak (4 PM: 22.3 percent late); early arrivals 9 to 17 percent. Counts
  are 3,700 to 11,000 arrivals per hour. Saturday has the same shape, gentler: 67.6 percent before 9 AM,
  60.2 midday, 58.9 afternoon and evening (late 9.5, 15.0, 18.2 percent).
- **Finding 4 relaxes slightly.** With three weekdays pooled, 67 of 280 route-directions have at least
  1,000 measured arrivals, 115 have 600 or more, 168 have 300 or more. Route comparisons become
  reasonable for the 67; keep the rule of showing the count and the uncertain slice with every figure and
  prefer about two weeks before naming routes.
- Caveats unchanged: GPS-inferred times (typically within about 15 s), the comparison with persistence
  shares our inference, and early arrivals at timepoints are not necessarily a problem (buses may wait).

## 2026-10-16

### 1. Metro's arrival predictions are no better than "the bus's current delay carries on"
Evidence: 2026-10-14, 286,727 / 200,511 / 91,062 stop arrivals at 5 / 15 / 30 minutes ahead, actual
arrival known within 60 s. Median absolute error, agency vs schedule vs persistence: 62 / 142 / 56 s
at 5 minutes, 96 / 156 / 94 s at 15, 131 / 173 / 133 s at 30 (decision 0014).
Caveat: persistence shares our inference with the "actual", so a systematic offset cancels for it and
counts against the agency; treat gaps under 10 to 15 s as unresolved. One weekday.

### 2. The prediction error follows the day's rhythm
Evidence: horizon 15 minutes, 2026-10-14, hourly (3,000+ rows each): agency median absolute error
73 s at 5 AM rising to 108 s at 5 PM; share within one minute 42 percent at 5 AM, 30 percent at 5 PM.
Persistence is within a few seconds of the agency in every hour. Caveat: hour 10 is affected by the
30 minute feed degradation that day.

### 3. Service is least punctual through the middle of the day and afternoon peak
Evidence: `common` lens (1 min early to 5 min late), timepoint arrivals by the Pacific hour a trip
starts, 2026-10-14. Definitely on time: 74 percent for 5 AM starts, 69 percent at 6 AM, 54 to 57
percent from 9 AM to 6 PM; late share 17 to 20 percent from 7 AM to 8 AM and 2 PM to 7 PM. The
uncertain slice is 9 to 24 points (largest at 9 AM, trips running into the 10:03 to 10:30 feed
degradation), so the true on-time share is higher than the definite one. Caveat: one weekday.

### 4. Route rankings are not yet possible
Evidence: on 2026-10-14 only 2 of 280 route-directions have 800 or more timepoint arrivals with an
interval and 114 have 200 or more. With 200 arrivals a share has about 3.5 points of sampling noise
before the uncertain slice. Rule: do not rank or name routes until each has well over 1,000
arrivals (roughly two weeks of data); show the count with every route figure.

### 5. About 40 trips a day are canceled in the feed
Evidence: 13 on 2026-10-13 (partial), 46 on 2026-10-14 (0.36 percent of 12,939 scheduled trips), 40 on
2026-10-15 (partial). Only 20 of the 135 trips announced but never seen on 10-14 are canceled, so most
unseen trips are something else (not investigated). See `docs/data_reports/kcm_realtime.md`.

### 6. The feed has gaps of its own
Evidence: 2026-10-14 17:03 to 17:30 UTC about 25 vehicles instead of about 600 (collector landed every
file; the source was thin); a nightly empty vehicle feed near 03:05 to 03:16 Pacific.
