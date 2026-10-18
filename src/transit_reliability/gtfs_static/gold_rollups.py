# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold roll-ups: route performance and prediction accuracy summaries built from gold detail tables.

`route_performance`: grain (service_date, route_id, direction_id, start_hour_pt), summing the
per-trip counts of `trip_performance` (counts are summed, never averaged percentages, so any share
can be recomputed with its sample size). A trip is attributed to the Pacific hour of its scheduled
start. `prediction_accuracy`: grain (service_date, route_id, target_hour_pt, horizon_min), quantiles
of the absolute prediction error for each predictor, from `prediction_errors` rows whose actual
arrival is known within 60 s. Both are rebuilt for the recent service dates each run.
"""

import datetime as dt
from typing import Any

from transit_reliability.reliability.lenses import LENSES

ROUTE_TABLE = "route_performance"
ACCURACY_TABLE = "prediction_accuracy"
RECOMPUTE_DAYS = 2
TIGHT_S = 60  # keep prediction rows whose actual arrival is known within this many seconds


def route_performance_sql(gold: str, day: dt.date) -> str:
    sums = ",\n      ".join(
        f"sum(n_{lens.name}_{k}) AS n_{lens.name}_{k}"
        for lens in LENSES
        for k in ("early", "on_time", "late", "uncertain")
    )
    return f"""
    SELECT service_date, route_id, first(route_short_name) AS route_short_name, direction_id,
      hour(from_utc_timestamp(scheduled_start, 'America/Los_Angeles')) AS start_hour_pt,
      count(*) AS n_trips,
      count_if(trip_status = 'observed') AS n_observed,
      count_if(trip_status = 'canceled') AS n_canceled,
      count_if(trip_status = 'not_observed') AS n_not_observed,
      sum(n_timepoints) AS n_timepoints, sum(n_timepoints_measured) AS n_timepoints_measured,
      {sums},
      sum(n_tp_departures_measured) AS n_tp_departures_measured, sum(n_tp_left_early) AS n_tp_left_early,
      percentile(median_delay_s, 0.5) AS median_of_trip_median_delays_s,
      percentile(first_stop_dep_delay_s, 0.5) AS median_first_stop_dep_delay_s,
      count(first_stop_dep_delay_s) AS n_first_stop_dep_measured
    FROM {gold}.trip_performance WHERE service_date = DATE'{day.isoformat()}'
    GROUP BY service_date, route_id, direction_id, hour(from_utc_timestamp(scheduled_start, 'America/Los_Angeles'))"""


def prediction_accuracy_sql(gold: str, day: dt.date) -> str:
    def block(prefix: str, column: str) -> str:
        return f"""
      percentile({column}, 0.5) AS {prefix}_median_error_s,
      percentile(abs({column}), 0.5) AS {prefix}_median_abs_error_s,
      percentile(abs({column}), 0.9) AS {prefix}_p90_abs_error_s,
      count_if(abs({column}) <= 60) AS {prefix}_n_within_60s"""

    return f"""
    SELECT service_date, route_id, hour(from_utc_timestamp(actual_arrival, 'America/Los_Angeles')) AS target_hour_pt,
      horizon_min, count(*) AS n,{block("kcm", "kcm_error_s")},{block("sched", "sched_error_s")},{block("persistence", "persistence_error_s")}
    FROM {gold}.prediction_errors
    WHERE service_date = DATE'{day.isoformat()}' AND width_s <= {TIGHT_S}
    GROUP BY service_date, route_id, hour(from_utc_timestamp(actual_arrival, 'America/Los_Angeles')), horizon_min"""


def _rebuild(
    spark: Any,
    source: str,
    target: str,
    sql_for: Any,
    prefix_label: str,
    rebuild_from: dt.date | None = None,
) -> None:
    from pyspark.sql import functions as F

    available = {
        r["service_date"] for r in spark.table(source).select("service_date").distinct().collect()
    }
    done = (
        {r["service_date"] for r in spark.table(target).select("service_date").distinct().collect()}
        if spark.catalog.tableExists(target)
        else set()
    )
    recent = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=RECOMPUTE_DAYS)
    for day in sorted(available):
        if day in done and day < recent and (rebuild_from is None or day < rebuild_from):
            continue
        frame = spark.sql(sql_for(day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").saveAsTable(target)
        rows = (
            spark.table(target)
            .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
            .count()
        )
        print(f"{prefix_label} {day}: {rows} rows")


def build_route_performance(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    gold = f"workspace.{prefix}_gold"
    _rebuild(
        spark,
        f"{gold}.trip_performance",
        f"{gold}.{ROUTE_TABLE}",
        lambda day: route_performance_sql(gold, day),
        "route performance",
        rebuild_from,
    )


def build_prediction_accuracy(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    gold = f"workspace.{prefix}_gold"
    _rebuild(
        spark,
        f"{gold}.prediction_errors",
        f"{gold}.{ACCURACY_TABLE}",
        lambda day: prediction_accuracy_sql(gold, day),
        "prediction accuracy",
        rebuild_from,
    )
