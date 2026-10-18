# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold prediction errors: how far off the agency's arrival predictions were, against baselines.

Grain: (service_date, trip_id, stop_sequence, horizon_min), one row per observed stop visit and
horizon where the agency prediction, the schedule baseline, and the persistence baseline are all
available. The rules (strictly no future information, only real predictions with a bus assigned
and already seen, canceled trips skipped, stale predictions skipped) are specified and tested in
`reliability/prediction.py`; this is the same logic in SQL, verified against it on a sample.
"""

import datetime as dt
from typing import Any

from transit_reliability.reliability.prediction import HORIZONS_MIN, MAX_STALE_S

TABLE = "prediction_errors"
RECOMPUTE_DAYS = 2


def prediction_errors_sql(silver: str, day: dt.date) -> str:
    horizons = ", ".join(str(h) for h in HORIZONS_MIN)
    d = day.isoformat()
    return f"""
    WITH horizons AS (SELECT explode(array({horizons})) AS horizon_min),
    obs AS (
      SELECT service_date, trip_id, route_id, stop_sequence, timepoint, arr_delay_s,
             CAST(sched_arr AS DOUBLE) AS sched, CAST(arr_est AS DOUBLE) AS actual,
             CAST(arr_hi AS DOUBLE) - CAST(arr_lo AS DOUBLE) AS width_s
      FROM {silver}.stop_visits WHERE service_date = DATE'{d}' AND arr_est IS NOT NULL),
    firstfix AS (
      SELECT trip_id, CAST(min(vehicle_ts) AS DOUBLE) AS first_fix
      FROM {silver}.vehicle_positions WHERE service_date = DATE'{d}' GROUP BY trip_id),
    targets AS (
      SELECT o.*, h.horizon_min, o.actual - h.horizon_min * 60.0 AS as_of
      FROM obs o CROSS JOIN horizons h JOIN firstfix f ON f.trip_id = o.trip_id
      WHERE o.sched IS NOT NULL AND f.first_fix <= o.actual - h.horizon_min * 60.0),
    kcm_all AS (
      SELECT t.trip_id, t.stop_sequence, t.horizon_min, t.as_of, s.vehicle_id, s.trip_schedule_relationship,
             CAST(s.arrival_time AS DOUBLE) AS pred, CAST(s.last_seen AS DOUBLE) AS last_seen,
             row_number() OVER (PARTITION BY t.trip_id, t.stop_sequence, t.horizon_min ORDER BY s.first_seen DESC) AS rn
      FROM targets t JOIN {silver}.trip_update_snapshots s
        ON s.service_date = t.service_date AND s.trip_id = t.trip_id AND s.stop_sequence = t.stop_sequence
      WHERE CAST(s.first_seen AS DOUBLE) <= t.as_of),
    kcm AS (
      SELECT * FROM kcm_all
      WHERE rn = 1 AND vehicle_id IS NOT NULL AND coalesce(trip_schedule_relationship, 0) <> 3
        AND pred IS NOT NULL AND as_of - last_seen <= {MAX_STALE_S}),
    pers_all AS (
      SELECT t.trip_id, t.stop_sequence, t.horizon_min, p.arr_delay_s AS prev_delay,
             row_number() OVER (PARTITION BY t.trip_id, t.stop_sequence, t.horizon_min ORDER BY p.actual DESC) AS rn
      FROM targets t JOIN obs p
        ON p.trip_id = t.trip_id AND p.stop_sequence < t.stop_sequence AND p.actual <= t.as_of AND p.arr_delay_s IS NOT NULL),
    pers AS (SELECT * FROM pers_all WHERE rn = 1)
    SELECT t.service_date, t.trip_id, t.route_id, t.stop_sequence, t.timepoint, t.horizon_min,
           timestamp_seconds(t.as_of) AS as_of, timestamp_seconds(t.actual) AS actual_arrival, t.width_s,
           k.pred - t.actual AS kcm_error_s,
           t.sched - t.actual AS sched_error_s,
           (t.sched + p.prev_delay) - t.actual AS persistence_error_s
    FROM targets t
    JOIN kcm k ON k.trip_id = t.trip_id AND k.stop_sequence = t.stop_sequence AND k.horizon_min = t.horizon_min
    JOIN pers p ON p.trip_id = t.trip_id AND p.stop_sequence = t.stop_sequence AND p.horizon_min = t.horizon_min"""


def build_prediction_errors(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    target = f"workspace.{prefix}_gold.{TABLE}"
    available = {
        r["service_date"]
        for r in spark.table(f"{silver}.stop_visits").select("service_date").distinct().collect()
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
        frame = spark.sql(prediction_errors_sql(silver, day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").saveAsTable(target)
        rows = (
            spark.table(target)
            .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
            .count()
        )
        print(f"prediction errors {day}: {rows} rows")
