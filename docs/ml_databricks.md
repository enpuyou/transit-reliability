# Machine learning on Databricks: what the industry pattern is, and what works on our free workspace

Checked 2026-10-22 by running two one-off jobs in our own workspace (`scripts/databricks_ml_smoke.py` and
`scripts/databricks_torch_smoke.py`, submitted with `databricks jobs submit`; the test model, experiment and
folder were deleted afterwards). The official Free Edition page says nothing about MLflow, the model registry,
GPUs or libraries, so these results are measurements, not documentation.

## The usual Databricks pattern, and how our plan maps onto it

| Step in the industry pattern | What Databricks provides | Our plan |
|---|---|---|
| Data lives in governed tables | Unity Catalog and Delta tables (bronze, silver, gold) | already how everything here works; the training table becomes a gold table |
| Features built once, reused | Feature Engineering tables with point-in-time lookups (a feature is read as it was at the example's time, which is exactly our "as of t" leakage rule) | available in the product generally; **not tested on Free Edition (unverified)**; until then our own SQL builds the as-of features and a test checks them |
| Experiments recorded | MLflow tracking: parameters, data window, scores, code version per run | **works** (tested) |
| Models versioned and governed | Models in Unity Catalog with versions and aliases such as "champion" | **works** (registered, reloaded and predicted in the test) |
| Training as a scheduled, reproducible job | Jobs and Asset Bundles (Databricks' MLOps Stacks template is bundle based; trunk-based branches, versioned artifacts) | we already deploy jobs this way; a training job is one more task |
| Predictions written back to a table | Batch inference: a job scores new data and writes a predictions table | the same shape as `gold.prediction_errors`, which already scores the agency |
| Monitoring for drift | Inference tables and Lakehouse Monitoring compare predictions with outcomes over time | we compare predictions with arrivals daily; the same table can hold our models |
| Serving live | Model Serving endpoints | **not usable for us**: the public map must never query Databricks, and the free plan limits endpoints and has no GPU serving; we export small models instead |

## Measured on this workspace (2026-10-22)

| Question | Result |
|---|---|
| Machine per job | 4 CPU cores, about 15 GB memory, Python 3.12, **no GPU** |
| Libraries (installed on demand with pip in the job environment) | XGBoost 3.4, LightGBM 4.7, CatBoost 1.2, MLflow 3.17, Optuna 5.0, pyGAM 0.12, scikit-learn 1.4, statsmodels 0.14, **PyTorch 2.14 (CPU only)** |
| Gradient-boosted trees, 1 million rows by 30 features, 200 trees | XGBoost 21.5 s, LightGBM 16.3 s |
| Small neural network (route and time-of-week embeddings plus 3 layers), 500,000 rows, 3 epochs | 10.8 s |
| Small LSTM, 200,000 sequences of 10 steps, 1 epoch | 30 s |
| MLflow tracking and Unity Catalog registry | a run logged and a model registered in `workspace.dev_transit_ml`, reloaded, predicted |
| A Delta table into pandas | 2 million rows of `gold.prediction_errors` in 11.6 s (421 MB) |
| Quota | unpublished; going over shuts compute down for the rest of the day (monthly in extreme cases); the test jobs were small |

## What this means

- **Our reproduction ladder fits.** Trees on a few million rows, classic models, a BusTr-style embedding network
  and an LSTM on sampled data all run in seconds to minutes on one 4-core machine.
- **Limits to plan around:** no GPU (so no large sequence models or transformers; keep neural nets small and
  train on samples); 15 GB memory (about 2 to 5 million rows of features in pandas at once); five concurrent job
  tasks; training is kept off the daily schedule at first and measured against the quota.
- **Untested here:** Feature Engineering point-in-time lookups, AutoML, Model Serving endpoints, distributed
  training on Spark (serverless is Spark Connect; single-machine libraries are the plan).
