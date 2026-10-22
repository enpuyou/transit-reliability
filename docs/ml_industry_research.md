# What industry really uses machine learning for in transit (research, 2026-10-22)

Gathered by a research subagent from web search results and a few fetched pages; most facts come from
search summaries, not from reading each full paper. Evidence labels: **independent** (outside evaluation),
**vendor** (the seller's or agency's own claim), **academic** (a paper, often one city, often not deployed),
**unverified** (could not confirm). Nothing here is measured on our data; it is context for
`docs/ml_design.md`.

## Summary table

| Use case | What is predicted | Typical model and data | Named real examples | Reported benefit | Evidence |
|---|---|---|---|---|---|
| Bus ETA, agency grade | Minutes until a bus reaches a stop | Per-route or per-segment ML on live position, time of day, weather | TransLink Vancouver (pilot on 13 routes, then system-wide); TriMet (copied it, open source); Swiftly at VIA San Antonio, MBTA, Dubai RTA | TransLink: error between predicted and actual departure down 74% in the pilot. Swiftly at VIA: accuracy 78% to 90%. MBTA: 75% to 84% (projected). Dubai: +24% | vendor / agency claim |
| Bus ETA where there is no live feed | Delay of a bus against its schedule, from traffic forecasts | Neural sequence model (BusTr) on road-traffic forecasts and schedules | Google Maps (most of the world's transit systems without official real-time tracking) | About 30% lower error than another research model; not compared with agency predictions | academic (by the deploying team) |
| Bus ETA, in-app ML | Arrival time | ML trained on past data | Transit app, first in Montreal, 2019 | Projected about 15% accuracy gain | vendor; no current documentation found |
| Road ETA (context) | Car or bus travel time | Graph neural network on the road network | Google Maps with DeepMind, 2020 | Cut "inaccurate" ETAs by 16% to 51% across 19 cities; baseline already about 97% accurate | vendor, peer reviewed |
| Academic bus ETA | Arrival or travel time | XGBoost, random forest, neural nets, LSTM, SVR+KNN | Dublin, Houston, NYC, Seattle and Trondheim, Phnom Penh | ML beats historical average in most papers (for example 5.7% vs 26% error, China); NYC's own predictions had 14.5% MAPE in a 2017 student project | academic, mixed baselines |
| Train delay for riders | Delay, "will I make my connection" | Retrained daily on timetable and live positions | Deutsche Bahn (T-Systems; DB Navigator and station displays); Bahnvorhersage (free open-data project) | no published benefit numbers found | unverified benefit |
| Bunching prediction and holding control | Will buses bunch; how long to hold | Regression, probabilistic models, rolling-horizon optimisation | Santiago (field trial), Porto (simulated), Miami-Dade (study) | Porto: up to 68% less bunching, in simulation only | academic; no long-running ML holding system at scale found |
| Crowding / occupancy | Seat availability by trip | Rider reports and passenger counters, then a model | Google Maps (2019, from rider reports); Transit app with LA Metro (88% as accurate as onboard counters) and Big Blue Bus pilot | 88% (Transit) | vendor |
| Ridership forecasting | Monthly or route ridership | Time series, LSTM, per-route nets | Chattanooga route study; heavy-rail study | Pre-COVID most models under 10% error; after COVID worse, one team's models were beaten by a naive baseline | academic |
| Runtime / schedule tuning | Realistic run times per period | Historical GPS and optimisation | Optibus Predictive Runtimes at Jacksonville JTA (Route 30) | On-time 83% to as high as 98% | vendor-partner post, unverified |
| Predictive maintenance | Which bus part will fail | Engine and sensor telematics | NYC Transit with Preteckt; Saskatoon; Pinellas; Ravenna | NYCT: 43% more maintenance productivity, alerts "over 80% accurate"; Ravenna: breakdowns down 8% | mostly vendor; a 2025 National Academies survey says quantified benefits are "still emerging" |
| Missed or cancelled trips | Whether a trip will run | (see below) | Paratransit reservations (gradient boosting won); Spare (on-demand rides) | Spare: over 90% (2019 blog) | **no fixed-route bus example found** |
| Rider feedback language analysis | Topic of a complaint | RoBERTa-type language models | WMATA (about 90% topic accuracy) | 90% | academic |

## Findings by area

- **How ETA is scored.** Agencies and vendors score the share of predictions that land inside a window around
  the actual arrival, not average error. WMATA's published definition: predictions up to 30 minutes ahead; a bus
  0 to 3 minutes away must arrive between 1 minute early and 1 minute late; a bus 12 to 30 minutes away between
  4 minutes early and 6 minutes late. Early counts as worse than late, because a rider who misses an early bus
  is stranded. Transit app has an "eta-accuracy-benchmark" with similar buckets (found only as a listing; not
  opened, details unverified). Papers use MAE, RMSE and MAPE; the two are not comparable, so a model can win on
  average error and lose on the agency's window score. King County Metro's own predictor was described in a UW
  capstone (2020) as a linear model on distance and speed.
- **Bunching.** Research finds it is largely made at the start of a route (irregular departure gaps; a late
  leading bus with short dwell for the follower; MBTA: concentrated at the last stops and peaks). It is more a
  control problem than a prediction problem. Prediction-based holding gives a better trade-off but is sensitive to
  prediction accuracy.
- **Crowding and demand** products rely on rider reports or passenger counters, not on bus-position data; we have
  no counters. Ridership forecasting got much harder after COVID-19.
- **Cancelled or missed trips:** no published example of ML predicting cancelled or no-show **fixed-route bus**
  trips. Found instead: a paper classifying already-missed trips for payment accountability, an edge-fog-cloud
  study detecting missing trips as they happen, paratransit reservation cancellations, on-demand ride
  cancellations. So our task B is novel.
- **How much ML beats simple baselines.** Bus ETA: ML beats historical average in nearly every paper, but the
  baselines differ, so gains are not comparable. Independent head-to-head evidence against an agency's own live
  predictions is thin: the TransLink, Swiftly and Transit numbers are self-reported. Gains are largest against weak
  baselines (schedule, old linear model) and smaller against a good live predictor (Google's own network work
  started from about 97% accurate ETAs). No clean study of weather's gain for bus travel time (ridership: about
  2.8 to 4.9% error reduction in one study). Aemmer et al. 2024 (University of Washington and SINTEF, Seattle and
  Trondheim open feeds): deep learning generalises within a network, as little as 1% loss on held-out routes.
- **Pitfalls the field reports:** overlapping windows from the same trip landing in both train and test sets (use
  time-ordered splits or hold out whole routes and days); using the agency's own stop-level prediction as an input
  may leak the answer (a question raised on the NSW open-data forum, not a verified finding); drift from schedule
  changes and COVID (Deutsche Bahn retrains every 24 hours); cold start for new routes (cross-route models work
  within a network); results from closed or one-route data rarely transfer; always report naive baselines.
  Feed latency and dropouts as a study topic: nothing found.

## What this means for our plan

- **A (delay at downstream stops):** strong precedent, the most studied task, boosted trees are among the winners.
  Beating "stays as late as now" is realistic at longer horizons. Beating the agency's own live predictions: no
  independent paper shows a third party doing it; treat it as a stretch goal and report a tie honestly. Add the
  agency-style **window score** next to average error.
- **B (will a trip run):** novel for fixed-route buses; no named example. Expect modest results (2 to 3% positives,
  rarely announced); judge by precision and recall at a fixed alert rate against simple baselines.
- **C (bunching):** published but mostly simulated; the evidence says look at where it starts.
- **D (explaining delay):** well precedented as analysis, not as a product.
- **Unrealistic or risky:** "beat the agency" as a headline claim; probability ranges (no deployed example, so test
  calibration); big gains from weather, events or 911 (no evidence of large effects; run ablations); random
  train and test splits.
- **Not feasible with our data:** crowding forecasts (no passenger counters), predictive maintenance (no engine
  data), ridership forecasts (no counts), battery range (no electric-bus data).
- **Industry tasks we could add, ranked:** (1) an honest ETA scoring dashboard in the agency's window format, by
  horizon, route and hour; (2) schedule runtime tuning analysis (actual run times against the timetable by period);
  (3) where bunching starts (first-stop departure gaps); (4) missed-trip detection as a labelled data product with
  an announcement-lead-time measure; (5) text analysis of alerts (low priority: no precedent found).

## Not found (unverified)

Deployed ML case studies for Moovit, Clever Devices, Trapeze, INIT, Uber or Lyft transit, TfL, Singapore LTA,
Hong Kong MTR; any fixed-route bus cancellation predictor; an independent third party beating an agency's ETA;
feed-latency studies; Google and Apple transit ETA internals beyond BusTr; Remix and Via outcome data.

## Sources

- Transit app ETA ML (TechCrunch 2019): https://techcrunch.com/2019/06/11/transit-city-navigation-app-seeks-to-solve-bad-public-transit-etas-with-machine-learning
- Transit crowding (2020): https://masstransitmag.com/home/press-release/21160934/transit-app-public-transit-riders-help-one-another-avoid-crowds-with-the-transit-app
- Big Blue Bus pilot (2021): https://santamonica.gov/press/2021/03/11/big-blue-bus-pilots-predictive-crowding-levels-for-all-lines-in-transit-app
- Swiftly at VIA (2020): https://www.goswift.ly/blog/vias-sms-story-how-san-antonio-improved-prediction-accuracy-by-15
- Swiftly at MBTA (2018): https://www.goswift.ly/press/mbta-selects-swiftly-to-provide-precise-real-time-transit-information-to-the-greater-boston-area
- Swiftly and Dubai RTA (2024): https://www.wam.ae/en/article/b5hbec6-rta-swiftly-partner-improve-accuracy-realtime
- County Connection Swiftly report (2023): https://countyconnection.com/wp-content/uploads/2023/06/4a.FINAL_Swiftly-Update.pdf
- TransLink: https://www.nsnews.com/news/translink-says-better-bus-departure-time-estimates-are-coming-1.23914669 and https://www.newwestrecord.ca/translink-adds-ai-program-to-improve-departure-estimates-1.23914719
- TriMet adaptation: https://vi.trimet.org/imi/pdf/Improve Arrival Predictions with Machine Learning.pdf
- Google BusTr: https://arxiv.org/abs/2007.00882
- DeepMind and Google Maps graph network (2020): https://deepmind.google/blog/article/traffic-prediction-with-advanced-graph-neural-networks ; paper: https://arxiv.org/pdf/2501.16656v1
- Google crowdedness (2019): https://9to5google.com/2019/06/27/google-maps-crowdedness-predictions/
- WMATA prediction accuracy definition: https://www.wmata.com/initiatives/open-data-hub/upload/SEDashboardDefinitions.pdf
- Transit app ETA benchmark listing: https://awesome.ecosyste.ms/projects/github.com%2Ftransitapp%2Feta-accuracy-benchmark
- Aemmer et al. 2024 (Seattle and Trondheim): https://www.sintef.no/en/publications/publication/2274121
- Dublin comparison: https://arxiv.org/pdf/2003.10373
- NYC student baseline (2017): https://cs229.stanford.edu/proj2017/final-reports/5229496.pdf
- KCM and UW capstone (2020): https://ise.washington.edu/news/article/2020-10-21/your-bus-eta-courtesy-machine-learning
- Bunching, Porto: https://www.researchgate.net/profile/Luis_Moreira-Matias/publication/265595729_An_Incremental_Probabilistic_Model_to_Predict_Bus_Bunching_in_Real-Time/links/5418434b0cf25ebee988117d.pdf
- Bunching field implementation: https://www.masstransitmag.com/technology/article/12358944/from-research-to-practice-implementing-real-time-control-to-avoid-bus-bunching
- Bunching causes (TriMet): https://pdxscholar.library.pdx.edu/cengin_fac/315
- Deutsche Bahn: https://railwaypro.com/wp/?p=53544 ; Bahnvorhersage: https://en.reset.org/missed-connection-bahnvohersage-uses-ai-open-data-to-predict-train-delays/
- Cancellation research: https://researchconnect.buffalo.edu/en/publications/predicting-trip-cancellations-and-no-shows-in-paratransit-operati/ ; https://spare.com/blog/cancellations-and-how-data-science-can-help ; https://arxiv.org/pdf/1711.09745
- Predictive maintenance: https://www.uitp.org/case-studies/expanding-prognostics-and-integrating-machine-learning-with-bus-telematics/ ; https://uwnxt.nationalacademies.org/read/29236/chapter/7
- Ridership forecasting after COVID: https://www.worldtransitresearch.info/research/11320
- Optibus: https://optibus.com/case/how-urbi-used-optibus-to-build-better-timetables-and-schedules-in-half-the-time/
- Rider feedback language analysis: https://arxiv.org/abs/2410.15016v1
- Weather and delay (DelayRadar): https://archive.isis.vanderbilt.edu/node/4788
- NSW forum on prediction versus actual delay: https://opendataforum.transport.nsw.gov.au/t/prediction-versus-actual-delay-arrival-or-departure/2696
