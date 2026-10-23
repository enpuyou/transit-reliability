# Machine learning results log

Dated, with sample sizes. Every number says which rows it was measured on. Nothing here is final; the data
covers a few weeks of one city. Method and plan: `docs/ml_design.md`. Scoring: `ml/scoring.py` (WMATA's windows:
a bus predicted 0 to 3 minutes away must arrive within 1 minute early to 1 minute late; 3 to 6: 1.5 early to 2
late; 6 to 12: 2.5 early to 3.5 late; 12 to 30: 4 early to 6 late).

## 2026-10-22 R0: baseline scoreboard, one day (service date 2026-10-20), rows where all four predictors answer

Window score (share of arrivals inside the window) / typical absolute error in seconds / bias in seconds (actual
minus predicted; positive = the bus came later than predicted), by the predictor's own minutes-away bucket.

| Predictor | 0-3 min | 3-6 min | 6-12 min | 12-30 min | All |
|---|---|---|---|---|---|
| Timetable | 28.0% / 186 / +172 | 44.4% / 181 / +138 | 64.7% / 186 / +127 | 83.7% / 186 / +109 | 64.9% / 185 / +127 |
| Stays as late as now (published information only) | 70.1% / 57 / +30 | 77.0% / 77 / +22 | 86.5% / 99 / +19 | 90.9% / 133 / +13 | 84.7% / 105 / +18 |
| Historical average (past days only) | 39.8% / 126 / +87 | 48.7% / 134 / +17 | 66.8% / 149 / -3 | 84.8% / 159 / -9 | 69.9% / 149 / +7 |
| Agency (KCM) | 70.3% / 57 / +23 | 75.5% / 78 / +10 | 85.0% / 100 / +2 | 89.7% / 134 / -9 | 83.7% / 106 / +1 |

Rows scored per bucket: 112,000 to 498,000 (each predictor puts a row in its own bucket). Reading: the agency and
"stays as late as now" are about equal and both far ahead of the timetable and the historical average; "stays as
late as now" is slightly ahead of the agency in every bucket from 3 minutes out (by 1.5 to 1.2 points in the window
score), and the agency is nearly unbiased while persistence runs about 18 seconds early-biased on predicted time
(buses come later than "as late as now" suggests). One day only; this is the bar the models must clear.

Caveats: one weekday; our arrival times are our own estimates (interval at most 60 s); the agency row needs a
fresh assigned prediction, which excludes the cases where the agency has none.
