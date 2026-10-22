# ruff: noqa: E501, E701, E702, E731, F841
"""Databricks capability check (decision: docs/ml_databricks.md). Run as a one-off job, not a normal script."""

import json
import os
import platform
import subprocess
import time

out = {}


def step(name, fn):
    t = time.time()
    try:
        out[name] = {"ok": True, "result": fn(), "seconds": round(time.time() - t, 1)}
    except Exception as e:  # noqa: BLE001
        out[name] = {
            "ok": False,
            "error": (type(e).__name__ + ": " + str(e))[:300],
            "seconds": round(time.time() - t, 1),
        }
    print(name, json.dumps(out[name])[:400], flush=True)


step(
    "machine",
    lambda: {
        "cpus": os.cpu_count(),
        "mem_gb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1),
        "python": platform.python_version(),
    },
)
step(
    "gpu",
    lambda: (
        subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip()
        or "no nvidia-smi output"
    ),
)


def libs():
    import importlib

    res = {}
    for m in (
        "mlflow",
        "xgboost",
        "lightgbm",
        "catboost",
        "sklearn",
        "torch",
        "optuna",
        "pandas",
        "numpy",
        "pygam",
        "statsmodels",
    ):
        try:
            res[m] = getattr(importlib.import_module(m), "__version__", "present")
        except Exception as e:  # noqa: BLE001
            res[m] = "missing: " + type(e).__name__
    return res


step("libraries_available", libs)


def train():
    import lightgbm as lgb
    import numpy as np
    import xgboost as xgb

    rng = np.random.default_rng(0)
    X = rng.normal(size=(1_000_000, 30))
    y = X[:, 0] * 3 + np.sin(X[:, 1]) * 2 + rng.normal(size=1_000_000)
    t = time.time()
    m = xgb.XGBRegressor(n_estimators=200, max_depth=8, tree_method="hist", n_jobs=-1).fit(X, y)
    a = round(time.time() - t, 1)
    t = time.time()
    boost = lgb.LGBMRegressor(n_estimators=200, num_leaves=127, n_jobs=-1, verbose=-1).fit(X, y)
    return {
        "xgboost_1M_rows_200_trees_seconds": a,
        "lightgbm_1M_rows_200_trees_seconds": round(time.time() - t, 1),
    }


step("train_1M_rows", train)


def tracking():
    import mlflow
    import numpy as np
    import xgboost as xgb

    exp = "/Users/youenpu@gmail.com/transit_ml_smoke"
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(exp)
    X = np.random.default_rng(1).normal(size=(5000, 5))
    y = X[:, 0] * 2
    with mlflow.start_run(run_name="smoke") as run:
        mlflow.log_params({"n_estimators": 20, "data_window": "synthetic"})
        mlflow.log_metric("mae", 0.12)
        model = xgb.XGBRegressor(n_estimators=20).fit(X, y)
        info = mlflow.xgboost.log_model(
            model,
            name="model",
            registered_model_name="workspace.dev_transit_ml.smoke_model",
            input_example=X[:2],
        )
    return {"run_id": run.info.run_id, "model_uri": info.model_uri}


step("mlflow_tracking_and_uc_registry", tracking)


def reload_model():
    import mlflow
    import numpy as np

    mlflow.set_registry_uri("databricks-uc")
    m = mlflow.pyfunc.load_model("models:/workspace.dev_transit_ml.smoke_model/1")
    return {"prediction_shape": list(m.predict(np.zeros((3, 5))).shape)}


step("load_registered_model", reload_model)


def spark_read():
    from pyspark.sql import SparkSession

    s = SparkSession.builder.getOrCreate()
    n = s.table("workspace.transit_gold.prediction_errors").limit(2_000_000).toPandas()
    return {"rows_to_pandas": len(n), "mem_mb": round(n.memory_usage(deep=True).sum() / 2**20)}


step("delta_table_to_pandas_2M_rows", spark_read)
print("RESULT_JSON " + json.dumps(out))
