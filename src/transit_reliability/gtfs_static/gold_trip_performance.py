# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold trip performance: one row per trip per service date, summarizing how it ran.

Built from silver stop visits. Grain: (service_date, trip_id). For a route on a day this lays the
trips out side by side (each with its start delay, typical and worst delay, how delay grew along the
trip, and an on-time breakdown) so they can be compared; route and hour summaries are computed from
these rows. Counts are always stored next to the percentages they feed.

On-time is judged under several lenses (`reliability/lenses.py`) using the *interval* of each
visit, so a visit that might fall either side of a limit is counted as uncertain, not guessed.
"""

import datetime as dt
from typing import Any

from transit_reliability.reliability.lenses import LENSES, Lens

TABLE = "trip_performance"
RECOMPUTE_DAYS = 2  # recent service dates are rebuilt each run; older ones once


def lens_count_columns(lens: Lens) -> str:
    """SQL for the four counts of timepoint arrivals under one lens (uses dlo/dhi columns)."""

    base = "timepoint = 1 AND dlo IS NOT NULL AND dhi IS NOT NULL"
    n = lens.name
    return f"""
      count_if({base} AND dhi < {lens.early_s}) AS n_{n}_early,
      count_if({base} AND dlo >= {lens.early_s} AND dhi <= {lens.late_s}) AS n_{n}_on_time,
      count_if({base} AND dlo > {lens.late_s}) AS n_{n}_late,
      count_if({base} AND NOT (dhi < {lens.early_s}) AND NOT (dlo > {lens.late_s})
               AND NOT (dlo >= {lens.early_s} AND dhi <= {lens.late_s})) AS n_{n}_uncertain"""


def trip_performance_sql(silver: str, day: dt.date) -> str:
    lens_sql = ",".join(lens_count_columns(lens) for lens in LENSES)
    return f"""
    WITH v AS (
      SELECT *,
             unix_timestamp(arr_lo) - unix_timestamp(sched_arr) AS dlo,
             unix_timestamp(arr_hi) - unix_timestamp(sched_arr) AS dhi,
             unix_timestamp(dep_hi) - unix_timestamp(sched_dep) AS dep_dhi
      FROM {silver}.stop_visits WHERE service_date = DATE'{day.isoformat()}'),
    canceled AS (
      SELECT DISTINCT trip_id FROM {silver}.trip_update_snapshots
      WHERE service_date = DATE'{day.isoformat()}' AND trip_schedule_relationship = 3),
    agg AS (
      SELECT service_date, trip_id,
        first(route_id) AS route_id, first(direction_id) AS direction_id,
        min_by(sched_dep, stop_sequence) AS scheduled_start,
        max_by(sched_arr, stop_sequence) AS scheduled_end,
        count(*) AS n_stops, count(arr_est) AS n_stops_measured,
        count_if(timepoint = 1) AS n_timepoints,
        count_if(timepoint = 1 AND arr_est IS NOT NULL) AS n_timepoints_measured,
        max(n_fixes) AS n_fixes,
        min_by(dep_delay_s, stop_sequence) AS first_stop_dep_delay_s,
        min_by(dep_method, stop_sequence) AS first_stop_dep_method,
        percentile(arr_delay_s, 0.5) FILTER (WHERE timepoint = 1 AND arr_delay_s IS NOT NULL) AS median_delay_s,
        percentile(arr_delay_s, 0.9) FILTER (WHERE timepoint = 1 AND arr_delay_s IS NOT NULL) AS p90_delay_s,
        max(arr_delay_s) FILTER (WHERE timepoint = 1) AS max_delay_s,
        min(arr_delay_s) FILTER (WHERE timepoint = 1) AS min_delay_s,
        min_by(arr_delay_s, stop_sequence) FILTER (WHERE timepoint = 1 AND arr_delay_s IS NOT NULL) AS first_tp_delay_s,
        max_by(arr_delay_s, stop_sequence) FILTER (WHERE timepoint = 1 AND arr_delay_s IS NOT NULL) AS last_tp_delay_s,
        count_if(timepoint = 1 AND dep_est IS NOT NULL) AS n_tp_departures_measured,
        count_if(timepoint = 1 AND dep_dhi < -60) AS n_tp_left_early,
        {lens_sql}
      FROM v GROUP BY service_date, trip_id)
    SELECT a.service_date, a.trip_id, a.route_id, r.route_short_name, a.direction_id,
           t.trip_headsign, t.block_id,
           a.scheduled_start, a.scheduled_end,
           CASE WHEN c.trip_id IS NOT NULL THEN 'canceled'
                WHEN a.n_fixes = 0 THEN 'not_observed' ELSE 'observed' END AS trip_status,
           a.n_stops, a.n_stops_measured, a.n_timepoints, a.n_timepoints_measured,
           a.first_stop_dep_delay_s, a.first_stop_dep_method,
           a.median_delay_s, a.p90_delay_s, a.max_delay_s, a.min_delay_s,
           a.first_tp_delay_s, a.last_tp_delay_s, a.last_tp_delay_s - a.first_tp_delay_s AS delay_growth_s,
           a.n_tp_departures_measured, a.n_tp_left_early,
           {", ".join(f"a.n_{lens.name}_{k}" for lens in LENSES for k in ("early", "on_time", "late", "uncertain"))}
    FROM agg a
    LEFT JOIN {silver}.scheduled_trips st ON st.service_date = a.service_date AND st.trip_id = a.trip_id
    LEFT JOIN {silver}.gtfs_trips t ON t.feed_version = st.feed_version AND t.trip_id = a.trip_id
    LEFT JOIN {silver}.gtfs_routes r ON r.feed_version = st.feed_version AND r.route_id = a.route_id
    LEFT JOIN canceled c ON c.trip_id = a.trip_id"""


def build_trip_performance(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    gold = f"workspace.{prefix}_gold"
    target = f"{gold}.{TABLE}"
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
        frame = spark.sql(trip_performance_sql(silver, day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").saveAsTable(target)
        trips = (
            spark.table(target)
            .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
            .count()
        )
        print(f"trip performance {day}: {trips} trips")
