# Findings log

Dated claims with their evidence and caveats. Nothing here is final: the data covers only a few
days (collection began 2026-10-13), times are GPS estimates (typically within about 15 s), and every
percentage should be read with its sample size. Status: **preliminary** unless stated.

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
