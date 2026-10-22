# Machine learning design (Phase 7): reproduce the leading systems, then see how we do

Rewritten 2026-10-22 after the owner's direction: **we do not invent prediction problems; we reproduce
well-known published or deployed machine learning for transit on our Seattle data, compare with what they
report and with simple baselines, then try stronger models.** "Will this trip run?" is dropped (no industry
precedent found). Research behind this: `docs/ml_industry_research.md`. Plain language: a **feature** is one
piece of information a model may look at; a **baseline** is a simple rule a model must beat; **leakage** is a
model seeing the future by accident.

## 1. The method

For each reproduction target: (1) take the published or deployed system, with its own data, features, model,
split and scoring as far as they are public; (2) rebuild it on our data as faithfully as our data allows,
and write down every difference; (3) score it the way the source did (so numbers can be compared) **and** the
way agencies score (window score, section 4); (4) add the baselines we already have; (5) then try stronger
models and features and record what each adds. A result of "could not reproduce the published gain" is a
finding, not a failure: **TriMet itself tried to copy TransLink's model and wrote that its models "did not
produce significantly improved vehicle predictions over non-predicted systems" and were not ready for
production** (TriMet IMI report summary, link in section 9), while TransLink reported 74 percent less error
in its own pilot.

## 2. The reproduction ladder

| # | Target (source) | What it predicts | Their method (what is public) | Our version | What we lack or change | Their reported result |
|---|---|---|---|---|---|---|
| R0 | **Evaluation harness**: WMATA's accuracy definition and Transit app's open ETA benchmark | any ETA, scored the agency way | share of predictions inside a window by minutes-ahead bucket; early counts worse than late | score the agency (KCM), the baselines and every model with it | check the Transit benchmark's exact buckets by reading its code (listing only seen so far) | defines the standard |
| R1 | **TransLink ML ETA, as copied by TriMet** (open code: `OpenTransitTools/transitcast`, `pytransitcast`) | bus arrival at later stops, from segment travel times | features: schedule (month, weekday, hour, minute, holiday, scheduled arrival, scheduled time between stops, schedule deviation), weather (rain, snow, temperature, dew point, humidity, pressure, wind, cloud cover, UV, visibility, drizzle), previous passage (how long the last bus took on this segment, how long ago), speed and traffic (INRIX speeds: current speed, travel time, age of the speed) | the same groups: schedule from GTFS; weather from our NWS stations (no rain amount, no snow, UV or cloud); previous passage from our own earlier buses; "traffic" from the speed of buses that just passed (no INRIX) | no INRIX or road traffic history (TomTom live only, not stored) | TransLink: error down 74 percent (pilot, own claim); **TriMet: no significant gain** |
| R2 | **BusTr, Google Maps** (Barnes et al., KDD 2020) | delay of a bus over a short piece of its trajectory | neural sequence model with embedded route, location cells and time of week, plus numeric traffic and schedule inputs; trained and tested by week | gradient-boosted trees and a small neural network with embeddings (route, stop, time of week) on the same kind of inputs; their ablations repeated (take out the real-time signal, route id, location, time of week) | Google's road traffic forecasts; we substitute the speed of recent buses and weather | MAPE 13.2 percent against 35.6 for "car traffic time only", 22.9 for linear regression, 31.5 for DeepTTE; ablations: without real-time traffic +16 percent error, without route ids +3, without all location +69, without time of week +5 |
| R3 | **BusTime, University College Dublin** (Liu et al., 2020; code public) | travel time from the trip start, then from every stop passed, to every later stop | five models on one route's GPS: "delay" (carry the current delay), k-nearest-neighbours, kernel regression, additive model, LSTM; stop-based sampling; **trips sorted by time, first 80 percent train, last 20 percent test**; MAE, MAPE, RMSE | the same five models on our data for one long frequent route and then for all routes, plus XGBoost and LightGBM | our reports are 20 s apart, theirs about 30 s; we have weeks, they had two months | LSTM best (MAE about 86 s, MAPE about 14 percent on 1,000 trips); "delay" cheapest but not accurate |
| R4 | **Cross-network generalisation** (Aemmer et al., 2024, University of Washington and SINTEF, Seattle and Trondheim open feeds) | bus travel time on routes or segments the model never saw | built on open GTFS-realtime only; four months from two cities | train on some routes, test on held-out routes (cold start for a new route) | need the paper's exact protocol (full text not read yet); Trondheim data not collected, so Seattle-only | deep learning generalises within a network, as little as 1 percent loss on held-out routes |
| R5 | **Bunching prediction** (Porto incremental probabilistic model; TriMet bunching causes) | will two buses be bunched at a later timepoint | regression and probabilistic models on the gap and recent trend; research says bunching is mostly made at the route start | predict bunched, gapped or regular at a downstream timepoint from the current gaps and trend (our headway tables), trees and logistic regression; first look at first-stop departure gaps | no holding or dispatch control to test | Porto: up to 68 percent less bunching in simulation only |
| R6 | **Dwell and delay prediction** (Deutsche Bahn, XGBoost) | extra time spent at a stop | gradient-boosted trees on station and time features | predict dwell at stops from our dwell estimates | our dwell is a GPS estimate, rough | mean error about 0.48 min; underestimates peak delays |
| R7 | **Runtime tuning** (Optibus Predictive Runtimes) | realistic run time per route piece and time of day | historical GPS and optimisation | from `gold.segment_runtimes`: what on-time would have been if the timetable used last week's median or 85th-percentile running times | counterfactual only; no real change to a timetable | vendor claims only |
| R8 | **Connection probability** (Deutsche Bahn Navigator, Bahnvorhersage) | will a rider make a transfer | delay predictions turned into a probability that the next vehicle has not left | between two bus trips at a shared stop, from the distribution of delays | optional, rider-facing; needs a transfer definition | no benefit numbers published |

Not reproduced, and why: crowding and occupancy (no passenger counters), predictive maintenance (no engine
data), ridership forecasting (no counts), electric-bus range (no data), Google and DeepMind's road-network
traffic model (no road graph or car traffic), trip-cancellation prediction (no fixed-route precedent).

## 3. Data, shared by all rungs

- **One training table** extending `gold.prediction_errors` (2.4 million scored agency predictions at 5 minutes
  ahead, 1.6 million at 15, 0.7 million at 30, for 2026-10-14 to 10-21). One row = a bus, a moment *t*, a later
  stop, with a label. Label quality: our arrival estimate from GPS; only stops whose interval is at most 60
  seconds wide are used (about 55,000 timepoint arrivals a weekday); errors under about 30 s are noise.
- **Feature groups** (each knows its source and whether it can be computed live on the map): now (lateness,
  trend, speed, age of report), trip so far, route memory (past days only), this exact trip's habit, neighbours
  (gap to the buses ahead and behind), the bus's previous trip, the agency's own prediction, disruption (skipped
  stops, detours, collisions and fires nearby), context (hour, weekday, weather, large events nearby).
- **Volume:** sampled (for example one moment every 2 minutes per trip) to a few million rows.
- **As of t** (leakage rule, section 6) applies to every feature.

## 4. How we score (decided before any result)

- **Two scoring languages.** (a) The source's own metric, so we can compare with their paper: MAPE for BusTr,
  MAE, MAPE and RMSE for Dublin. (b) The agency window score (R0): WMATA's rule, a bus 0 to 3 minutes away must
  arrive within 1 minute early to 1 minute late, a bus 12 to 30 minutes away within 4 minutes early to 6 minutes
  late. Average error and window score can disagree; we report both.
- **Chronological split only.** Earlier days train, the newest week tests (Dublin and BusTr both do this); a
  validation week before it is used for choices; the test week is touched once per model version; a rolling
  check moves the window forward a week at a time.
- **Same rows for everyone.** Models and baselines are scored on exactly the same examples.
- **Baselines (measured 2026-10-14 to 10-21, truth interval at most 60 s, typical / bad error in seconds):**
  5 minutes ahead: KCM 61 / 167, "stays as late as now" 56 / 150, timetable 139 / 421; 15 minutes: 94 / 250,
  92 / 242, 152 / 450; 30 minutes: 129 / 343, 129 / 346, 169 / 501. The simple rule already matches the agency.
- **Is a gain real?** Confidence by resampling whole days of trips, not rows. Following BusTr, repeat neural
  training several times and report the spread (their 20 runs and t-tests; one DeepTTE variant failed to
  converge in 37 percent of runs).
- **Ablation tables** like BusTr's: remove one feature group at a time and report the loss in accuracy.
- **Slices:** by horizon, hour, frequent or not, weather, with or without disruptions. A model that wins on
  average and loses at rush hour is not a win.

## 5. Models, from simple to strong

1. **Baselines:** timetable, "stays as late as now" (Dublin's "delay"), historical average by route, stop and
   hour, and the agency's own prediction.
2. **Classic:** linear regression and a generalised additive model (R3), k-nearest-neighbours and kernel
   regression (R3), random forest.
3. **Gradient-boosted trees: XGBoost, LightGBM, CatBoost.** The main workhorse. They handle mixed information
   and missing values, train on a few million rows in minutes on one machine, show which features mattered, and
   can give a range with quantile versions. Exportable as plain trees for the browser.
4. **Neural networks:** a small network with embeddings for route, stop and time of week (BusTr's idea); an
   LSTM or GRU over a bus's recent stops (Dublin's best); if time allows, a small transformer. Tuning with a
   proper search (Optuna) on the validation week only.
5. **Ensembles:** average or stack the best of each family.

**Compute (measured 2026-10-22, `docs/ml_databricks.md`):** Databricks Free Edition serverless gives a job 4 CPU
cores and about 15 GB of memory, **no GPU**. XGBoost trains 1 million rows by 30 features with 200 trees in 22
seconds and LightGBM in 16; a small embedding network takes 11 seconds for 500,000 rows and 3 epochs; a small LSTM
takes 30 seconds per epoch on 200,000 sequences; PyTorch (CPU), CatBoost, Optuna and pyGAM install on demand.
So trees and small networks on a few million sampled rows are comfortable; large neural models are not. The daily
quota is unpublished, so training runs are measured and kept off the daily schedule at first.
**Tracking:** MLflow tracking and the Unity Catalog model registry both work (tested): each run records the data window, feature
list, settings, code version and scores. Reproduced numbers are written to `docs/ml_results.md` with dates.

## 6. Not fooling ourselves

- **"As of t" means visible at t, not stamped before t.** Bus reports reach us a median 57 s after their GPS
  time. Every feature is built from data whose *publication* time (first appearance in the feed, kept in bronze)
  is at or before t; an arrival estimate only counts as known once the later report that fixes it was published.
- **History features use only days before the example's day.** A test checks, for a sample of rows, that every
  feature timestamp is at or before t; the build fails otherwise.
- **The agency's own prediction as an input** can leak the answer once a bus has passed (a question raised on
  the NSW open-data forum, unverified); we use only the prediction visible at t, and test with and without it.
- **Overlapping examples from the same trip** must never straddle train and test.

## 7. Feasibility and honest expectations

- **Data:** about 8 complete days now; three weeks for a first honest result (about 2026-11-03); weather,
  events and incidents need more weeks. Early rungs (R0, R3, the R1 and R2 pipelines on one route) can start now.
- **The agency is a hard baseline**: independent papers do not show an outsider beating an agency's live
  predictions; wins are vendors beating older systems, and TriMet's copy did not win. A tie, with honest
  ranges and a clear account of when the agency is wrong, is a real result.
- **Weather, events, 911:** no evidence of large gains in the literature; we test each by ablation and report
  small or zero effects plainly.
- **Three weeks of one city is thin**; every table says so and gives sample sizes.

## 8. Order of work

1. R0: the scoring harness, with the agency and baselines scored on the same rows (now).
2. The training table and the leakage test (now).
3. R3: the five Dublin models plus XGBoost and LightGBM on one route, chronological 80/20 (now; needs little data).
4. R1 and R2 pipelines on the same table, first with one route, then all routes (about 2026-11-03 for honest numbers).
5. Ablations; neural models; R4 (held-out routes).
6. R5, R6, R7, R8.
7. Write-up and a scorecard page on the site, dated, with sample sizes.
8. If a model clearly wins and uses only live features, export it for the map.

## 9. Sources

- TriMet, "Improve Arrival Predictions with Machine Learning Model" (adapted from TransLink; open code
  OpenTransitTools/transitcast and pytransitcast): https://vi.trimet.org/imi/pdf/Improve%20Arrival%20Predictions%20with%20Machine%20Learning.pdf
- Barnes et al., "BusTr: Predicting Bus Travel Times from Real-Time Traffic", KDD 2020: https://arxiv.org/abs/2007.00882
- Liu, Sun, Wang, "BusTime: Which is the Right Prediction Model for My Bus Arrival Time?", 2020: https://arxiv.org/abs/2003.10373
- Aemmer et al., "Generalization strategies for improving bus travel time prediction across networks", Journal
  of Urban Management 2024: https://sintef.no/en/publications/publication/2274121
- WMATA prediction accuracy definition: https://www.wmata.com/initiatives/open-data-hub/upload/SEDashboardDefinitions.pdf
- More, with evidence quality, in `docs/ml_industry_research.md`.

## 10. Decisions for the owner

- Start now with R0 (harness), the training table and R3 (Dublin's five models plus XGBoost)? (recommended)
- Is the "could not reproduce" outcome acceptable as a finding for R1 (TriMet saw it)?
- Publish the scorecard on the public site as results come?
