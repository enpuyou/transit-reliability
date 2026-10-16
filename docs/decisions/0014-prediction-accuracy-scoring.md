# 0014: Score the agency's arrival predictions against inferred arrivals, with baselines

Date: 2026-10-16
Status: accepted (built, verified, in prod as `transit_gold.prediction_errors`)

## Context

One of the project's five questions is how accurate the agency's predictions are and whether a
simple method does as well. This needs the history of Metro's predictions (`trip_update_snapshots`)
and when buses actually reached stops (`stop_visits`). Earlier findings shape the rules: before a
bus is assigned a published "prediction" is just the timetable (decision 0010), and our "actual"
arrival is itself an estimate (typically within about 15 s, decision 0011).

## Decision

For every stop visit with an inferred arrival time T and each horizon H in 5, 15, 30 minutes:
- **The prediction at horizon H** is the latest prediction published at or before T - H, never later
  information. It counts only if a bus was assigned to the trip, the trip is not canceled, it has an
  arrival time, and it was last confirmed no more than 120 s before T - H.
- **Only after the bus is seen on the trip:** the trip's first GPS fix must be at or before T - H.
- **Baselines on identical rows:** the schedule alone (predict zero delay), and persistence (the
  delay of the bus's most recent observed stop before T - H carries on to the target stop).
- **Error = predicted minus actual** (seconds); positive means predicted too late.
- Rows exist only where all three predictors are available, so they are compared fairly. The table
  keeps `width_s` (our uncertainty about the actual arrival) so analyses can drop loose ones.
- The rules are specified and tested in `reliability/prediction.py`; the SQL in
  `gold_prediction_errors.py` mirrors them. Recent service dates are rebuilt each run.

## Verification

On a sandbox slice (300 random trips of 2026-10-14) the Spark output equals the Python reference
exactly: 15,576 rows on each side, none on only one side, zero differing values. An earlier
apparent mismatch was my check pulling only the first page of a large SQL API result (see the
guide), not the pipeline. Built in prod for three service dates (79 s).

## First results (2026-10-14, full day, actual arrival known within 60 s) - preliminary

Median error, 90th percentile of absolute error, and share within one minute:

| Horizon | n | Agency | Schedule | Persistence |
|---|---|---|---|---|
| 5 min | 286,727 | bias +13 s, median abs 62 s, p90 170 s, 49% within 60 s | bias -113 s, 142 s, 427 s, 24% | bias +1 s, 56 s, 151 s, 53% |
| 15 min | 200,511 | +11 s, 96 s, 257 s, 33% | -121 s, 156 s, 456 s, 22% | -7 s, 94 s, 249 s, 34% |
| 30 min | 91,062 | -5 s, 131 s, 348 s, 25% | -128 s, 173 s, 506 s, 20% | -28 s, 133 s, 360 s, 25% |

Reading: the agency's predictions are far better than the timetable (buses run on average 2 minutes
late, the schedule's bias), error grows with horizon, and "the current delay carries on" is as
good as the agency's prediction at 15 and 30 minutes and slightly better at 5. That is consistent
with the earlier observation that the agency's predictor appears to carry a bus's delay forward.
A model has to beat persistence, not only the schedule.

## Caveats (important)

- **The comparison favors persistence somewhat.** Persistence uses delays from our own inference
  and is scored against our own inferred arrivals, so a systematic offset in our inference cancels
  for it but counts against the agency's independent prediction. The agency's median bias of +13 s
  at 5 minutes could partly be that. Treat differences of 10 to 15 s as unresolved.
- One day, one weekday, with the feed degraded for 30 minutes on it (decision 0011); not yet a
  stable result. Weekly patterns and route differences are not examined.
- The sample is "a bus is assigned and already seen", which excludes the cases where the agency
  has little information, so it measures predictions when they are real predictions.

## Consequences

- Phase 7's benchmark exists: any model is scored on these rows against both baselines.
- Next analyses: error by route, hour, and distance to the stop; whether the agency's error is
  worse for long trips or after a gap; repeat over more days before stating conclusions.
