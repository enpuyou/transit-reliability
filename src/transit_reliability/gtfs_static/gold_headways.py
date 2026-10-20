# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold headways: how evenly buses are spaced at timepoints (bunching and gaps).

`headway_pairs`: grain (service_date, route, direction, timepoint stop, trip), pairing each trip with
the trip scheduled just before it at the same stop. The actual headway is the difference of the two
departure estimates, kept as an interval; the class (`bunched`, `gapped`, `regular`, `uncertain`) is
`reliability.lenses.classify_headway` and its SQL twin below. Only scheduled gaps up to an hour are kept;
`frequent` marks pairs scheduled 4 to 15 minutes apart, the only ones where bunching is meaningful.
`headway_summary`: counts per route, direction, time band and day type over the last 28 complete days
(counts, never averaged shares). Decision 0023.
"""

import datetime as dt
from typing import Any

from transit_reliability.gtfs_static.gold_segments import (
    COMPLETE_AFTER_DAYS,
    RECOMPUTE_DAYS,
    WINDOW_DAYS,
    band_sql,
    day_type_sql,
)
from transit_reliability.reliability.lenses import (
    BUNCHED_BELOW,
    FREQUENT_MAX_S,
    FREQUENT_MIN_S,
    GAPPED_ABOVE,
)

PAIRS_TABLE = "headway_pairs"
SUMMARY_TABLE = "headway_summary"
MAX_SCHEDULED_S = 3600  # pairs scheduled further apart than an hour are not kept
MIN_PAIRS = 10
CLASSES = ("bunched", "gapped", "regular", "uncertain")


def headway_class_sql(sched: str = "sched_h", lo: str = "act_lo", hi: str = "act_hi") -> str:
    """SQL twin of `classify_headway`; NULL when the actual headway has no bounds."""

    return f"""CASE
        WHEN {sched} <= 0 OR {lo} IS NULL OR {hi} IS NULL THEN NULL
        WHEN {hi} < {BUNCHED_BELOW} * {sched} THEN 'bunched'
        WHEN {lo} > {GAPPED_ABOVE} * {sched} THEN 'gapped'
        WHEN {lo} >= {BUNCHED_BELOW} * {sched} AND {hi} <= {GAPPED_ABOVE} * {sched} THEN 'regular'
        ELSE 'uncertain' END"""


def headway_pairs_sql(prefix: str, day: dt.date) -> str:
    silver = f"workspace.{prefix}_silver"
    d = day.isoformat()
    hour = "hour(from_utc_timestamp(sched_dep, 'America/Los_Angeles'))"
    return f"""
    WITH tp AS (
      SELECT service_date, route_id, direction_id, stop_id, trip_id, sched_dep, dep_est, dep_lo, dep_hi
      FROM {silver}.stop_visits WHERE service_date = DATE'{d}' AND timepoint = 1 AND dep_est IS NOT NULL),
    pairs AS (
      SELECT service_date, route_id, direction_id, stop_id, trip_id, sched_dep, dep_est, dep_lo, dep_hi,
        lag(trip_id) OVER (PARTITION BY route_id, direction_id, stop_id ORDER BY sched_dep, trip_id) AS prev_trip_id,
        lag(sched_dep) OVER (PARTITION BY route_id, direction_id, stop_id ORDER BY sched_dep, trip_id) AS prev_sched_dep,
        lag(dep_est) OVER (PARTITION BY route_id, direction_id, stop_id ORDER BY sched_dep, trip_id) AS prev_dep_est,
        lag(dep_lo) OVER (PARTITION BY route_id, direction_id, stop_id ORDER BY sched_dep, trip_id) AS prev_dep_lo,
        lag(dep_hi) OVER (PARTITION BY route_id, direction_id, stop_id ORDER BY sched_dep, trip_id) AS prev_dep_hi
      FROM tp),
    h AS (
      SELECT service_date, route_id, direction_id, stop_id, trip_id, prev_trip_id,
        CAST(unix_timestamp(sched_dep) - unix_timestamp(prev_sched_dep) AS DOUBLE) AS sched_h,
        CAST(unix_timestamp(dep_est) - unix_timestamp(prev_dep_est) AS DOUBLE) AS act_h,
        CAST(unix_timestamp(dep_lo) - unix_timestamp(prev_dep_hi) AS DOUBLE) AS act_lo,
        CAST(unix_timestamp(dep_hi) - unix_timestamp(prev_dep_lo) AS DOUBLE) AS act_hi,
        {hour} AS sched_hour_pt
      FROM pairs WHERE prev_trip_id IS NOT NULL)
    SELECT service_date, route_id, direction_id, stop_id, trip_id, prev_trip_id, sched_h, act_h, act_lo, act_hi,
           (sched_h BETWEEN {FREQUENT_MIN_S} AND {FREQUENT_MAX_S}) AS frequent,
           {headway_class_sql()} AS outcome,
           {band_sql("sched_hour_pt")} AS band, {day_type_sql("service_date")} AS day_type
    FROM h WHERE sched_h > 0 AND sched_h <= {MAX_SCHEDULED_S}"""


def headway_summary_sql(prefix: str, last_day: dt.date) -> str:
    silver, gold = f"workspace.{prefix}_silver", f"workspace.{prefix}_gold"
    first = last_day - dt.timedelta(days=WINDOW_DAYS - 1)
    counts = ", ".join(f"count_if(outcome = '{c}') AS n_{c}" for c in CLASSES)
    return f"""
    WITH agg AS (
      SELECT route_id, direction_id, band, day_type, count(*) AS n_pairs, count(DISTINCT service_date) AS n_days,
             {counts},
             percentile(sched_h, 0.5) AS sched_headway_median_s, percentile(act_h / sched_h, 0.5) AS ratio_p50,
             percentile(act_h / sched_h, 0.1) AS ratio_p10, percentile(act_h / sched_h, 0.9) AS ratio_p90,
             percentile(act_hi - act_lo, 0.5) AS median_uncertainty_s
      FROM {gold}.{PAIRS_TABLE}
      WHERE frequent AND service_date BETWEEN DATE'{first.isoformat()}' AND DATE'{last_day.isoformat()}'
      GROUP BY route_id, direction_id, band, day_type HAVING count(*) >= {MIN_PAIRS}),
    routes AS (SELECT route_id, max_by(route_short_name, feed_version) AS route_short_name FROM {silver}.gtfs_routes GROUP BY route_id)
    SELECT a.route_id, r.route_short_name, a.* EXCEPT (route_id), DATE'{last_day.isoformat()}' AS built_through
    FROM agg a LEFT JOIN routes r ON r.route_id = a.route_id"""


def build_headway_pairs(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    target = f"workspace.{prefix}_gold.{PAIRS_TABLE}"
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
        frame = spark.sql(headway_pairs_sql(prefix, day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)
        rows = (
            spark.table(target)
            .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
            .count()
        )
        print(f"headway pairs {day}: {rows} rows")


def build_headway_summary(spark: Any, prefix: str) -> None:
    last_day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    target = f"workspace.{prefix}_gold.{SUMMARY_TABLE}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {headway_summary_sql(prefix, last_day)}")
    print(f"headway summary through {last_day}: {spark.table(target).count()} rows")
