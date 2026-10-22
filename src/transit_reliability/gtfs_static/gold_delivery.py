# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold trip delivery: did each scheduled trip run, and if not, did the agency say so?

`trip_delivery`: one row per scheduled trip and service date. A trip RAN if any bus position was matched
to it that day. The agency's prediction file can mark a whole trip canceled and single stops skipped
(silver.trip_update_snapshots keeps those marks and when they first appeared). Outcomes:

- ran: a bus reported on it;
- ran, skipped stops: it ran but the agency marked at least one of its stops as skipped;
- canceled with notice: it did not run and was marked canceled at least NOTICE_MIN minutes before its start;
- canceled late: it did not run and was marked canceled later than that (or after its start);
- did not run, not announced: no bus ever reported on it and it was never marked canceled.

"Did not run" means no bus position was matched: a bus running without its tracker on looks the same
(unverified share). Each row also says whether the same bus's previous trip (the timetable's block)
ran, because a missing trip is often followed by another (84% on 2026-10-20).

`trip_delivery_summary`: the last 28 complete service dates, counted by route, time band and day type.
"""

import datetime as dt
from typing import Any

from transit_reliability.gtfs_static.gold_segments import band_sql, day_type_sql

TABLE = "trip_delivery"
SUMMARY = "trip_delivery_summary"
NOTICE_MIN = 15
RECOMPUTE_DAYS = 2
WINDOW_DAYS = 28
COMPLETE_AFTER_DAYS = 2


def trip_delivery_sql(prefix: str, day: dt.date) -> str:
    silver = f"workspace.{prefix}_silver"
    d = day.isoformat()
    pt = "from_utc_timestamp(s.sched_start, 'America/Los_Angeles')"
    return f"""
    WITH trips AS (
      SELECT st.service_date, st.trip_id, st.route_id, st.direction_id, g.block_id, g.service_id
      FROM {silver}.scheduled_trips st JOIN {silver}.gtfs_trips g ON g.feed_version = st.feed_version AND g.trip_id = st.trip_id
      WHERE st.service_date = DATE'{d}'),
    starts AS (
      SELECT trip_id, min(scheduled_departure_ts) AS sched_start FROM {silver}.scheduled_stop_times
      WHERE service_date = DATE'{d}' GROUP BY trip_id),
    seen AS (SELECT DISTINCT trip_id FROM {silver}.vehicle_positions WHERE service_date = DATE'{d}' AND trip_id IS NOT NULL),
    marks AS (
      SELECT trip_id,
             max(CASE WHEN trip_schedule_relationship = 3 THEN 1 ELSE 0 END) AS canceled,
             min(CASE WHEN trip_schedule_relationship = 3 THEN first_seen END) AS canceled_at,
             count(DISTINCT CASE WHEN stop_schedule_relationship = 1 THEN stop_sequence END) AS skipped_stops,
             max(CASE WHEN vehicle_id IS NOT NULL AND vehicle_id <> '' THEN 1 ELSE 0 END) AS bus_named
      FROM {silver}.trip_update_snapshots WHERE service_date = DATE'{d}' GROUP BY trip_id),
    s AS (
      SELECT t.*, st.sched_start, (seen.trip_id IS NOT NULL) AS ran, coalesce(m.canceled, 0) = 1 AS canceled, m.canceled_at,
             coalesce(m.skipped_stops, 0) AS skipped_stops, coalesce(m.bus_named, 0) = 1 AS bus_named,
             lag(t.trip_id) OVER w AS prev_trip_id, lag(seen.trip_id IS NOT NULL) OVER w AS prev_ran
      FROM trips t JOIN starts st ON st.trip_id = t.trip_id
      LEFT JOIN seen ON seen.trip_id = t.trip_id LEFT JOIN marks m ON m.trip_id = t.trip_id
      WINDOW w AS (PARTITION BY t.block_id, t.service_id ORDER BY st.sched_start))
    SELECT s.service_date, s.trip_id, s.route_id, s.direction_id, s.block_id, s.sched_start,
           hour({pt}) AS start_hour_pt, {band_sql(f"hour({pt})")} AS band, {day_type_sql("s.service_date")} AS day_type,
           s.ran, s.canceled, s.canceled_at,
           CASE WHEN s.canceled_at IS NULL THEN NULL ELSE (unix_timestamp(s.sched_start) - unix_timestamp(s.canceled_at)) / 60.0 END AS notice_min,
           s.skipped_stops, s.bus_named, s.prev_trip_id, s.prev_ran,
           CASE WHEN s.ran AND s.skipped_stops > 0 THEN 'ran, skipped stops'
                WHEN s.ran THEN 'ran'
                WHEN s.canceled AND unix_timestamp(s.sched_start) - unix_timestamp(s.canceled_at) >= {NOTICE_MIN} * 60 THEN 'canceled with notice'
                WHEN s.canceled THEN 'canceled late'
                ELSE 'did not run, not announced' END AS outcome
    FROM s"""


def trip_delivery_summary_sql(prefix: str, last_day: dt.date) -> str:
    gold = f"workspace.{prefix}_gold"
    silver = f"workspace.{prefix}_silver"
    first = last_day - dt.timedelta(days=WINDOW_DAYS - 1)
    return f"""
    WITH d AS (SELECT * FROM {gold}.{TABLE} WHERE service_date BETWEEN DATE'{first.isoformat()}' AND DATE'{last_day.isoformat()}'),
    routes AS (SELECT route_id, max_by(route_short_name, feed_version) AS route_short_name FROM {silver}.gtfs_routes GROUP BY route_id)
    SELECT d.route_id, r.route_short_name, d.band, d.day_type, count(*) AS n_scheduled,
           sum(CASE WHEN outcome = 'ran' THEN 1 ELSE 0 END) AS n_ran,
           sum(CASE WHEN outcome = 'ran, skipped stops' THEN 1 ELSE 0 END) AS n_ran_skipped_stops,
           sum(CASE WHEN outcome = 'canceled with notice' THEN 1 ELSE 0 END) AS n_canceled_with_notice,
           sum(CASE WHEN outcome = 'canceled late' THEN 1 ELSE 0 END) AS n_canceled_late,
           sum(CASE WHEN outcome = 'did not run, not announced' THEN 1 ELSE 0 END) AS n_not_run_not_announced,
           sum(CASE WHEN prev_ran = false THEN 1 ELSE 0 END) AS n_after_missing_previous,
           sum(CASE WHEN prev_ran = false AND NOT ran THEN 1 ELSE 0 END) AS n_missing_after_missing_previous,
           count(DISTINCT service_date) AS n_days, min(service_date) AS first_day, max(service_date) AS last_day
    FROM d LEFT JOIN routes r ON r.route_id = d.route_id
    GROUP BY d.route_id, r.route_short_name, d.band, d.day_type"""


def build_trip_delivery(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    target = f"workspace.{prefix}_gold.{TABLE}"
    available = {
        r["service_date"]
        for r in spark.table(f"{silver}.vehicle_positions")
        .select("service_date")
        .distinct()
        .collect()
    }
    done = (
        {r["service_date"] for r in spark.table(target).select("service_date").distinct().collect()}
        if spark.catalog.tableExists(target)
        else set()
    )
    recent = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=RECOMPUTE_DAYS)
    for day in sorted(d for d in available if d is not None):
        if day in done and day < recent and (rebuild_from is None or day < rebuild_from):
            continue
        frame = spark.sql(trip_delivery_sql(prefix, day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)
        rows = spark.table(target).where(
            F.col("service_date") == F.lit(day.isoformat()).cast("date")
        )
        print(
            f"trip delivery {day}: {rows.count()} trips, {rows.where('NOT ran').count()} did not run"
        )


def build_trip_delivery_summary(spark: Any, prefix: str) -> None:
    last_day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    target = f"workspace.{prefix}_gold.{SUMMARY}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {trip_delivery_summary_sql(prefix, last_day)}")
    print(f"trip delivery summary through {last_day}: {spark.table(target).count()} rows")
