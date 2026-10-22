# Machine learning design (Phase 7)

Written 2026-10-22, before any model is trained. It replaces `docs/ml_plan.md` as the plan of record (that
file keeps the first reasoning). Plain language: a **feature** is one piece of information a model may look
at; a **label** is the answer it learns to give; a **baseline** is a simple rule a model must beat to be worth
anything.

## 1. What we are training for, in order

| # | Question | Who it helps | Kind of model | When |
|---|---|---|---|---|
| A | **When will this bus reach that stop?** (5, 15 and 30 minutes ahead) | riders, the live map | regression with a range (quantiles) | first model, data ready about 2026-11-03 |
| B | **Will this trip run at all?** (asked 30, 15 and 5 minutes before its start) | riders waiting at a terminal; the map's departures panel | yes/no (classification) | can start now: 8 complete days, a clear signal |
| C | Will these two buses end up bunched downstream? | frequent-route riders, agency dispatch | yes/no | after A |
| D | What explains delay (weather, events, incidents, bunching, time of day)? | the write-up | simple, readable models; association not cause | after rain and a few weeks of events |

A is the design doc's main model. **B is new**, from this week's finding: about 2 to 3 percent of trips on
tracked routes do not run, 95 percent of those are never announced, and a missing trip is followed by another
84 percent of the time. A rider cannot see that coming today; a model can.

Not in scope: anything that needs data we do not have (ridership, crowding), and a "magic" model that beats
the agency by a lot (see section 7).

## 2. Question A: arrival time at a downstream stop

**One example** = a bus, a moment *t* (the "as of" time), a stop ahead on its trip, and a horizon (the stop is
due about 5, 15 or 30 minutes after *t*). The same structure as `gold.prediction_errors`, which already holds
2.4 million scored agency predictions at 5 minutes, 1.6 million at 15 and 0.7 million at 30 for 2026-10-14 to
10-21, so the training table extends a table we trust instead of starting fresh.

**Label:** how late the bus will be at that stop (our estimated arrival minus the timetable), in seconds. The
model predicts the *change* from the lateness it has now, which is easier and keeps it honest.

**Label quality:** the "truth" is our own arrival estimate from GPS, with an interval. Training and scoring use
only stops whose interval is at most 60 seconds wide (about 55,000 timepoint arrivals a weekday), and errors
below about 30 seconds are treated as noise.

**Output:** a single best guess plus a range: the 10th and 90th percentile ("arrives 5:12 to 5:16, usually
5:14"). A range that is right 80 percent of the time is more useful to a rider than one number.

### Features (all must be known **at time t**; see section 5)

| Group | Features | Why it should help | Source |
|---|---|---|---|
| Now | lateness at the last stop passed; how it changed over the last 2-3 timepoints; speed; stopped or moving; age of the newest report | delay persists and trends | silver.stop_visits, vehicle_positions |
| Trip so far | share of the trip done, stops and distance left, scheduled time left | room for delay to change | scheduled_stop_times |
| Route memory | typical and 85th-percentile running time of the coming segments at this time of day and day type (past days only), how often they run over | some stretches are always slow at 5 pm | gold.segment_runs, computed per training window |
| This exact trip | how late this trip usually is at that stop (past days) | trips have habits | the per-trip history built for the map |
| Neighbours | gap to the bus ahead and behind; is the bus ahead late | bunching: a bus right behind another runs fast | positions of other buses on the route |
| Block | did this bus's previous trip run, how late did it finish | lateness carries over from trip to trip | gold.trip_delivery, stop_visits |
| Agency | the agency's own prediction and how it changed | the model can learn when the agency is wrong | silver.trip_update_snapshots |
| Disruption | skipped stops ahead; detour alert on the route; a collision or fire near the coming stops in the last hour | the timetable is wrong in these cases | trip_update_snapshots, alerts, gold.incident_routes |
| Context | hour, day of week, holiday; weather this hour; a large event near the coming stops in the next or last 2 hours | traffic and crowds | weather_hourly, gold.event_routes |
| Identity | route, direction | the basic shape of each route | |

**Baselines, scored on exactly the same rows** (already computed in `gold.prediction_errors`, 2026-10-14 to 10-21,
truth intervals at most 60 s):

| Horizon | Agency: typical / bad (90th pct) error | "Stays as late as now": typical / bad | Timetable: typical / bad |
|---|---|---|---|
| 5 min | 61 s / 167 s | 56 s / 150 s | 139 s / 421 s |
| 15 min | 94 s / 250 s | 92 s / 242 s | 152 s / 450 s |
| 30 min | 129 s / 343 s | 129 s / 346 s | 169 s / 501 s |

The simple rule "the bus stays as late as it is now" is already as good as the agency (slightly better at 5 and
15 minutes; the agency is at least as good as it only 45 to 50 percent of the time). That is the bar.

**Model:** gradient-boosted trees (LightGBM): handles mixed information, missing values and non-linear effects,
trains in minutes on a few million rows on one machine, and says which features mattered. Three quantile
models (10th, 50th, 90th percentile) per horizon, or one model with horizon as a feature (decided by the first
experiment).

## 3. Question B: will this trip run?

**One example** = a scheduled trip and a moment 30, 15 or 5 minutes before its scheduled start (on tracked
routes only). **Label:** did it run (a bus reported on it), from `gold.trip_delivery`. About 2 to 3 percent of
examples are "did not run", so this is a rare-event problem: accuracy is meaningless (always saying "it runs"
is 97 percent accurate); the measures are how many missing trips we catch (recall) and how many of our
warnings are right (precision).

**Features at that moment:** the previous trip of the same bus (is it running now, how late, did it finish,
did it go quiet early, did it never appear); whether the agency has named a bus for this trip; trips of this
route already missing today; cancellations and skipped stops on the route today; hour, day type, route; the
block's earlier trips.

**Baselines:** the rule the map uses now ("previous trip never showed up": 84 percent of such trips did not run
on 2026-10-20) and "no bus named yet". The model is worth it only if it catches more missing trips at the same
precision, or warns earlier.

**Model:** logistic regression first (readable), then gradient-boosted trees; probabilities checked for
calibration ("70 percent likely to run" should come true about 70 percent of the time).

## 4. How we judge (decided now, before any result, so we cannot move the goalposts)

- **Split by time, never at random.** Train on earlier days, test on the newest week. Random splits leak:
  two moments of the same trip a minute apart look alike. Model choices (features, settings) use a
  validation week before the test week; the test week is touched once per model version.
- **Rolling check:** repeat with the window moved forward a week at a time (train weeks 1-2, test 3; train
  2-3, test 4) to see if gains hold.
- **A: measures:** median and 90th-percentile absolute error per horizon; the share of predictions within 60 s;
  for the range, how often the truth falls inside the 10th-90th (should be about 80 percent) and how wide it is.
- **B: measures:** precision and recall at a few warning thresholds, the area under the precision-recall curve,
  and calibration.
- **Same rows for everyone.** Model and baselines are scored on exactly the same examples.
- **Is a gain real?** Confidence intervals by resampling whole trip-days (not single rows, which are not
  independent). A gain smaller than its interval is reported as "no clear gain".
- **Where it wins or loses:** by horizon, time of day, route type (frequent or not), weather, with and without
  disruptions. A model that wins on average but loses badly at rush hour is not shipped.
- **Ablation:** add feature groups one at a time and record what each adds. Often one group does most of the
  work; that is a finding.
- **Promotion rule:** a new model replaces the current one only if it is better on the newest week and not
  worse at any horizon.

## 5. Not fooling ourselves (leakage)

- **"As of t" means visible at t, not stamped before t.** Bus reports reach us a median 57 seconds after their
  GPS time; a real-time model can only use a report once it was published. Every feature is built from data
  whose *publication* time (the first feed that contained it, kept in bronze) is at or before *t*. Our
  arrival estimates use the report *after* a stop, so an arrival only counts as known once that later report
  was published.
- **History features from the past only:** typical running times, per-trip habits and route summaries are
  recomputed from days before the example's day.
- **A test checks it:** for a sample of rows, every feature timestamp is at or before *t*; the build fails
  otherwise.
- **Label leakage in B:** "the previous trip never appeared" must be judged with what was visible at *t*, not
  with the day's final answer.

## 6. Feasibility

- **Data:** enough now for B (8 complete days, about 2,400 missing trips) and for building A's pipeline; three
  weeks for a first honest A result (about 2026-11-03); rain and event effects need more weeks.
- **Volume:** A's table is millions of rows a day if every moment is used; we sample (for example one moment
  every 2 minutes per trip) to a few million rows for training. Building it is a daily Databricks job like the
  others.
- **Compute (Free Edition):** feature tables are SQL in the daily job; training a few-million-row model takes
  minutes on one serverless machine (LightGBM added as a job dependency). The daily quota is not published, so
  training runs are measured and kept off the daily schedule at first.
- **Tracking:** MLflow works on our workspace (checked 2026-10-22): each run records the data window, feature
  list, settings, code version and scores.
- **Using a model live:** the public map must never query Databricks. A trained tree model can be exported as a
  small file (JSON trees) and evaluated in the browser or the relay, using only features the page can compute
  live (positions, predictions, static history files). So we mark each feature "live" or "offline only"; the
  live model may be a smaller version of the best offline one. Until then, models are evaluated offline and
  reported on the site.

## 7. Honest expectations

- The agency is already about as good as "stays as late as now". A realistic win for A is a modest drop in the
  bad cases (the 90th percentile) at 15 and 30 minutes, plus calibrated ranges, plus a clear account of when
  the agency is wrong. If a model does not beat persistence, that is a result worth publishing: it measures
  how predictable delay is from public data.
- B is likely the bigger rider win: a missing trip is predictable from the block, and no one tells riders today.
- Three weeks is thin for rain and events; the scorecard will say so.

## 8. What we build, in order

1. **B's training table and baselines** (now): one row per trip and moment before start, features as visible
   then, label from trip_delivery; score the two rules. Then logistic regression and trees.
2. **A's training table** (now, extending prediction_errors): features as visible at *t*, the leakage test,
   and the baseline scoreboard on the same rows.
3. **A's first model** (about 2026-11-03): a small feature set (lateness now, its trend, time left, hour,
   route), then feature groups one at a time.
4. **Reports:** a scorecard on the site (baselines, model, slices, ranges), dated and with sample sizes.
5. **Live use:** export the best "live-computable" model and use it in the map, if it clearly wins.
6. C and D when their data is ready.

## 9. Decisions for the owner

- Start with B (will this trip run) now, and build A's table in parallel? (recommended)
- Is a model that only matches the agency on average but gives honest ranges worth showing on the map?
- Publish model scores on the public site as they come (recommended), or keep them internal until A is trained?
