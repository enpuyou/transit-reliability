# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold segment running times: how long buses really take between timepoints versus the timetable.

`segment_runs`: grain (service_date, trip_id, from timepoint, to timepoint) for consecutive timepoints
of a trip. Observed seconds = arrival at the next timepoint minus departure from this one (our
estimates, each with an interval); scheduled seconds = scheduled arrival at the next minus scheduled
departure here. `segment_runtimes`: the trailing 28 complete service dates summarised by route
segment, time band and day type (quantiles are recomputed from the runs, they cannot be pooled from
daily summaries). A run is flagged `detour_alert` when the agency published a reroute or detour
alert naming its route that day: on a detour the bus leaves the route line, so our estimates are weaker
and the timetable is known to be wrong (decision 0022).
"""

import datetime as dt
from typing import Any

RUNS_TABLE = "segment_runs"
RUNTIMES_TABLE = "segment_runtimes"
RECOMPUTE_DAYS = 2
WINDOW_DAYS = 28
COMPLETE_AFTER_DAYS = 2  # a service date is complete this many days later (after-midnight trips)
MIN_RUNS = 10  # a segment summary needs at least this many runs

# An alert names a route like "Route 77", "Routes 75 and 77", "Routes 77 and 75". The prefix allows a
# list of other routes before it; the suffix stops "77" from matching inside "773" or "77th".
ROUTE_PREFIX = r"(?i)\broutes?\s+(?:[A-Za-z0-9]+\s*(?:,|and|&)\s*)*"
ROUTE_SUFFIX = r"(?![0-9A-Za-z])"
DETOUR_WORDS = r"(?i)(rerout|detour)"


def route_mention_pattern(short_name: str) -> str:
    """The regular expression that matches an alert headline naming this route."""

    return f"{ROUTE_PREFIX}{short_name}{ROUTE_SUFFIX}"


def band_sql(hour: str) -> str:
    """Pacific time band of an hour-of-day expression."""

    return (
        f"CASE WHEN {hour} BETWEEN 6 AND 8 THEN 'am_peak' WHEN {hour} BETWEEN 9 AND 14 THEN 'midday' "
        f"WHEN {hour} BETWEEN 15 AND 18 THEN 'pm_peak' WHEN {hour} BETWEEN 19 AND 23 THEN 'evening' ELSE 'night' END"
    )


def day_type_sql(date: str) -> str:
    return f"CASE dayofweek({date}) WHEN 1 THEN 'sunday' WHEN 7 THEN 'saturday' ELSE 'weekday' END"


def segment_runs_sql(prefix: str, day: dt.date) -> str:
    silver, bronze = f"workspace.{prefix}_silver", f"workspace.{prefix}_bronze"
    d = day.isoformat()
    pt = "from_utc_timestamp(dep_est, 'America/Los_Angeles')"
    prefix_sql, suffix_sql = ROUTE_PREFIX.replace("\\", "\\\\"), ROUTE_SUFFIX
    detour_words = DETOUR_WORDS.replace("\\", "\\\\")
    return f"""
    WITH tp AS (
      SELECT service_date, trip_id, route_id, direction_id, stop_sequence, stop_id, sched_dep, dep_est, dep_lo, dep_hi,
        lead(stop_sequence) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS nxt_seq,
        lead(stop_id) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS nxt_stop,
        lead(sched_arr) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS nxt_sched_arr,
        lead(arr_est) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS nxt_arr_est,
        lead(arr_lo) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS nxt_arr_lo,
        lead(arr_hi) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS nxt_arr_hi
      FROM {silver}.stop_visits WHERE service_date = DATE'{d}' AND timepoint = 1),
    seg AS (
      SELECT service_date, trip_id, route_id, direction_id, stop_id AS from_stop_id, nxt_stop AS to_stop_id,
             stop_sequence AS from_stop_sequence, nxt_seq AS to_stop_sequence,
             CAST(unix_timestamp(nxt_sched_arr) - unix_timestamp(sched_dep) AS DOUBLE) AS sched_s,
             CAST(unix_timestamp(nxt_arr_est) - unix_timestamp(dep_est) AS DOUBLE) AS obs_s,
             CAST((unix_timestamp(nxt_arr_hi) - unix_timestamp(dep_lo)) - (unix_timestamp(nxt_arr_lo) - unix_timestamp(dep_hi)) AS DOUBLE) AS obs_width_s,
             hour({pt}) AS start_hour_pt
      FROM tp
      WHERE nxt_stop IS NOT NULL AND dep_est IS NOT NULL AND nxt_arr_est IS NOT NULL
        AND unix_timestamp(nxt_sched_arr) > unix_timestamp(sched_dep)),
    names AS (SELECT route_id, max_by(route_short_name, feed_version) AS short_name FROM {silver}.gtfs_routes GROUP BY route_id),
    headlines AS (
      SELECT DISTINCT header_text FROM {bronze}.alerts
      WHERE feed_header_ts >= unix_timestamp(TIMESTAMP'{d} 00:00:00') - 86400 AND feed_header_ts < unix_timestamp(TIMESTAMP'{d} 00:00:00') + 2 * 86400
        AND to_date(from_utc_timestamp(timestamp_seconds(feed_header_ts), 'America/Los_Angeles')) = DATE'{d}'
        AND header_text RLIKE '{detour_words}'),
    detoured AS (
      SELECT DISTINCT n.route_id FROM names n JOIN headlines h
        ON h.header_text RLIKE concat('{prefix_sql}', n.short_name, '{suffix_sql}'))
    SELECT s.service_date, s.trip_id, s.route_id, s.direction_id, s.from_stop_id, s.to_stop_id,
           s.from_stop_sequence, s.to_stop_sequence, s.sched_s, s.obs_s, s.obs_width_s, s.start_hour_pt,
           {band_sql("s.start_hour_pt")} AS band, {day_type_sql("s.service_date")} AS day_type,
           (dr.route_id IS NOT NULL) AS detour_alert
    FROM seg s LEFT JOIN detoured dr ON dr.route_id = s.route_id"""


def segment_runtimes_sql(prefix: str, last_day: dt.date) -> str:
    silver, gold = f"workspace.{prefix}_silver", f"workspace.{prefix}_gold"
    first = last_day - dt.timedelta(days=WINDOW_DAYS - 1)
    return f"""
    WITH agg AS (
      SELECT route_id, direction_id, from_stop_id, to_stop_id, band, day_type,
             count(*) AS n, count(DISTINCT service_date) AS n_days,
             percentile(sched_s, 0.5) AS sched_median_s,
             percentile(obs_s, 0.5) AS obs_p50_s, percentile(obs_s, 0.85) AS obs_p85_s, percentile(obs_s, 0.95) AS obs_p95_s,
             percentile(obs_s - sched_s, 0.5) AS excess_p50_s,
             avg(CASE WHEN obs_s > sched_s THEN 1.0 ELSE 0.0 END) AS share_over_schedule,
             percentile(obs_width_s, 0.5) AS median_uncertainty_s,
             avg(CASE WHEN detour_alert THEN 1.0 ELSE 0.0 END) AS detour_share,
             min(service_date) AS first_day, max(service_date) AS last_day
      FROM {gold}.{RUNS_TABLE}
      WHERE service_date BETWEEN DATE'{first.isoformat()}' AND DATE'{last_day.isoformat()}'
      GROUP BY route_id, direction_id, from_stop_id, to_stop_id, band, day_type
      HAVING count(*) >= {MIN_RUNS}),
    routes AS (SELECT route_id, max_by(route_short_name, feed_version) AS route_short_name FROM {silver}.gtfs_routes GROUP BY route_id),
    stops AS (SELECT stop_id, max_by(stop_name, feed_version) AS stop_name FROM {silver}.gtfs_stops GROUP BY stop_id)
    SELECT a.route_id, r.route_short_name, a.direction_id, a.from_stop_id, s1.stop_name AS from_stop_name,
           a.to_stop_id, s2.stop_name AS to_stop_name, a.band, a.day_type, a.n, a.n_days,
           a.sched_median_s, a.obs_p50_s, a.obs_p85_s, a.obs_p95_s, a.excess_p50_s, a.share_over_schedule,
           a.median_uncertainty_s, a.detour_share, a.first_day, a.last_day
    FROM agg a LEFT JOIN routes r ON r.route_id = a.route_id
    LEFT JOIN stops s1 ON s1.stop_id = a.from_stop_id LEFT JOIN stops s2 ON s2.stop_id = a.to_stop_id"""


def build_segment_runs(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    target = f"workspace.{prefix}_gold.{RUNS_TABLE}"
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
        frame = spark.sql(segment_runs_sql(prefix, day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)
        rows = (
            spark.table(target)
            .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
            .count()
        )
        print(f"segment runs {day}: {rows} rows")


def build_segment_runtimes(spark: Any, prefix: str) -> None:
    """Rebuild the trailing-window summary (small; replaced whole each run)."""

    last_day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    target = f"workspace.{prefix}_gold.{RUNTIMES_TABLE}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {segment_runtimes_sql(prefix, last_day)}")
    n = spark.table(target).count()
    print(f"segment runtimes through {last_day}: {n} segment/band/day-type rows")
