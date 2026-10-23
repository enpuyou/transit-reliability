# 0028: The arrival-time training table, the "as of t" rule, and the baseline scoreboard

Date: 2026-10-22
Status: accepted; built, verified on one full day, deployed with the next prod deploy.

## Why a new table

`gold.prediction_errors` scores the agency at fixed horizons (5, 15, 30 minutes) by defining the moment as the
ACTUAL arrival minus the horizon. For scoring that is fine. For a model it hands over the answer ("the bus
arrives exactly 15 minutes after now"), so a model trained on it would look brilliant and be useless. The
training table therefore picks moments on a global clock grid (every 6 minutes, the same for every trip),
without looking at the outcome, and asks about every stop still ahead within 40 minutes.

## The "as of t" rule

Every feature uses only information that had been published by the example's moment, not merely stamped before
it. Reports reach us a median 57 s after their GPS time, so using the stamp would leak about a minute.

- Earlier stops of the same trip count from `visible_at`: the first time the report that fixes the arrival
  (the first report after the bus passed the stop) appeared in the agency feed (bronze `feed_header_ts`).
- The agency's prediction is the newest snapshot first seen at or before the moment.
- History (typical lateness at this stop, time band and day type) comes only from earlier service dates.
- Weather is the last complete hour before the moment.
- Audit columns (`state_visible_at`, `kcm_first_seen`, `hist_last_day`) let seven daily checks prove it
  (`ml_examples_leak_*`, `ml_examples_duplicate_keys`, `ml_examples_rows`). **Checked that the checks can fail:**
  planting each of the five leaks in a copy of the table was caught every time (counts of 52 to 10,073 rows).

## What a row holds (v1)

Label: `lateness_s` (how late the bus was at the stop; our arrival estimate, interval at most 60 s). Features:
time to the scheduled arrival, hour, weekday, day type, time band; where the bus is "now" (lateness at the latest
visible stop, the stop before it, the step between them, stops to go, how old that information is, scheduled gap
to the target); the agency's prediction and its age and whether a bus is assigned; history; last complete
hour's weather class. Not yet: neighbour gaps, the previous trip of the bus, disruption and event features.

One full day (2026-10-20): 1,216,469 rows from 12,375 trips, built in 51 s on the 2X-Small warehouse. The information
about where the bus is has a median age of 79 s (5th to 95th percentile 8 to 225 s); the targets are a median
12 minutes ahead (1 to 34 minutes for the middle 90 percent).

## The scoreboard

Baselines scored on identical rows (all four must answer: the agency had a fresh, assigned, uncancelled
prediction, and history exists): the timetable, "stays as late as it is now", the historical average, the
agency. Scored the agency's way (WMATA windows, `ml/scoring.py`) and with MAE; the daily sums are kept so
intervals resample whole days.

## Finding: the old persistence baseline was slightly optimistic

`gold.prediction_errors` takes the delay of the latest stop whose arrival time is at or before the moment,
ignoring when that arrival could have been known. On a 1-in-12 sample of 2026-10-20 trips (94,000 rows): window
score 86.0 percent against 84.4 percent when only published information is used, typical error 103 s against
107 s. The honest baseline is the published-only one used here. (The agency comparison in decision 0014 stands, but
the persistence side of it flattered the simple baseline by about 1.6 points.)
