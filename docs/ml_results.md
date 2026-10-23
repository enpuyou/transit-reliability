# Machine learning results log

Dated, with sample sizes. Every number says which rows it was measured on. Nothing here is final; the data
covers a few weeks of one city. Method and plan: `docs/ml_design.md`. Scoring: `ml/scoring.py` (WMATA's windows:
a bus predicted 0 to 3 minutes away must arrive within 1 minute early to 1 minute late; 3 to 6: 1.5 early to 2
late; 6 to 12: 2.5 early to 3.5 late; 12 to 30: 4 early to 6 late).

## 2026-10-22 R0: baseline scoreboard, six weekdays (2026-10-14 to 10-21, without the weekend)

Production table `ml.baseline_scores_daily`, rows where all four predictors answer (fresh assigned agency
prediction, history of at least 5 past observations, complete days). Weekend days are not scored yet: the history
predictor needs an earlier weekend (the first one is Oct 17-18, so Oct 24-25 is the first scorable weekend).
Intervals resample the six days (95 percent). Window score = share of arrivals inside WMATA's window for the
predictor's own minutes-away bucket; error in seconds; bias = actual minus predicted (positive = came later).

| Predictor | Window score, all buckets | Typical absolute error | Bias | Rows |
|---|---|---|---|---|
| Timetable | 64.5% [63.2, 65.9] | 185 s | +125 s | 5.29 M |
| Historical average (past days only) | 68.7% [67.8, 69.5] | 152 s | -3 s | 5.58 M |
| Agency (KCM) | 83.8% [83.6, 83.9] | 106 s | -2 s | 5.85 M |
| **Stays as late as now (published information only)** | **84.7% [84.4, 84.9]** | **104 s** | +16 s | 5.85 M |

By window, persistence minus agency (window score points, with a 95 percent day-resampled interval):

| Minutes away | Persistence | Agency | Difference |
|---|---|---|---|
| 0-3 | 70.5% | 71.1% | -0.5 [-0.9, -0.05] |
| 3-6 | 77.1% | 75.7% | +1.4 [+1.1, +1.6] |
| 6-12 | 86.3% | 84.9% | +1.4 [+1.3, +1.6] |
| 12-30 | 90.7% | 89.5% | +1.2 [+1.1, +1.3] |

Reading: the agency and "stays as late as now" are within about one point of each other, but the intervals say the
difference is real and small: persistence ahead by about 1.2 to 1.4 points at 3 minutes and beyond, the agency
ahead by about half a point in the last 3 minutes. Both are about 19 points ahead of the timetable. The agency is
nearly unbiased; persistence runs 16 seconds toward "predicted earlier than the bus came". **This is the bar any
model must clear.** An earlier one-day figure (Oct 20: persistence 84.7, agency 83.7) matches. Six days of one city;
resampling days does not capture week-to-week changes; weekends are missing.
