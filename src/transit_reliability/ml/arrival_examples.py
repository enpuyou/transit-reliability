# ruff: noqa: E501  (SQL is kept on readable lines)
"""The training table for arrival-time prediction: one row = a bus, a moment, a stop still ahead.

Table `<prefix>_ml.arrival_examples`. Built per service date, rebuilt for recent dates.

WHAT A ROW IS. A moment `as_of` on a global clock grid (every GRID_S seconds, the same for every trip, so
the moments do not depend on how the trip turned out), and a stop of a trip that had not been reached
yet. The label is how late the bus was at that stop (`lateness_s`, from our arrival estimate). Only stops
whose arrival interval is at most MAX_WIDTH_S wide are targets (label quality). Targets are 0 to
HORIZON_MAX_S seconds ahead of `as_of` by the actual arrival, so the model is trained on the whole range
a rider might ask about.

WHY NOT THE OLD DESIGN. `gold.prediction_errors` fixes `as_of` as the actual arrival minus the horizon
(5, 15, 30 minutes). That is fine for scoring the agency, but for a model it hands over the answer: "the bus
arrives exactly 15 minutes after now". Here the moment is chosen without looking at the outcome.

NO PEEKING (the "as of t" rule). Every feature comes from information that had been PUBLISHED at or before
`as_of`, not merely stamped before it:
- the bus's earlier stops: an arrival estimate counts only from `visible_at`, the time the report that fixes
  it (the first report after the bus passed the stop) first appeared in the agency feed (bronze
  `feed_header_ts`); reports reach us a median 57 s after their GPS time, so using the stamp would leak;
- the agency's prediction: the newest snapshot first seen at or before `as_of`;
- history (typical lateness at this stop, time of day and day type): only service dates BEFORE this one;
- weather: the last COMPLETE hour before `as_of`.
Freshness of the agency's prediction (`kcm_age_s`) is time since it was last confirmed, never below zero: a
negative value would say "this prediction stayed unchanged for N more seconds after the moment", which is the
future (found and fixed 2026-10-22: the first version had it, and no check caught it until a row was read by hand).
The audit columns `state_visible_at`, `kcm_first_seen` and `hist_last_day` let the checks prove it
(`ml_examples_leak_*` in audit/catalog.py): none may be later than `as_of` (or this service date).
"""

import datetime as dt
from typing import Any

from transit_reliability.gtfs_static.gold_segments import band_sql, day_type_sql

TABLE = "arrival_examples"
GRID_S = 360  # one moment every 6 minutes
HORIZON_MAX_S = 2400  # targets up to 40 minutes ahead
MAX_WIDTH_S = 60  # label quality: arrival interval at most this wide
STATE_WIDTH_S = 180  # earlier stops used as "where the bus is now" may be a little looser
HISTORY_DAYS = 28
RECOMPUTE_DAYS = 2


def arrival_examples_sql(prefix: str, day: dt.date) -> str:
    silver, bronze = f"workspace.{prefix}_silver", f"workspace.{prefix}_bronze"
    d = day.isoformat()
    pt_hour = "hour(from_utc_timestamp(sv.sched_arr, 'America/Los_Angeles'))"
    return f"""
    WITH vis AS (
      SELECT trip_id, vehicle_ts, min(feed_header_ts) AS published
      FROM {bronze}.vehicle_positions
      WHERE trip_id IS NOT NULL
        AND feed_header_ts BETWEEN unix_timestamp(TIMESTAMP'{d} 06:00:00') AND unix_timestamp(TIMESTAMP'{d} 06:00:00') + 32 * 3600
      GROUP BY trip_id, vehicle_ts),
    firstpub AS (SELECT trip_id, min(published) AS first_published FROM vis GROUP BY trip_id),
    visits AS (
      SELECT sv.service_date, sv.trip_id, sv.route_id, sv.direction_id, sv.stop_sequence, sv.stop_id, sv.timepoint,
             CAST(sv.sched_arr AS DOUBLE) AS sched, CAST(sv.arr_est AS DOUBLE) AS actual, sv.arr_delay_s AS delay_s,
             CAST(sv.arr_hi AS DOUBLE) - CAST(sv.arr_lo AS DOUBLE) AS width_s, CAST(v.published AS DOUBLE) AS visible_at
      FROM {silver}.stop_visits sv
      LEFT JOIN vis v ON v.trip_id = sv.trip_id AND v.vehicle_ts = unix_timestamp(sv.arr_hi)
      WHERE sv.service_date = DATE'{d}' AND sv.arr_est IS NOT NULL AND sv.sched_arr IS NOT NULL AND sv.arr_delay_s IS NOT NULL),
    targets AS (
      SELECT t.*, explode(sequence(
          CAST(ceil((t.actual - {HORIZON_MAX_S}) / {GRID_S}) * {GRID_S} AS BIGINT),
          CAST(floor((t.actual - 1) / {GRID_S}) * {GRID_S} AS BIGINT), {GRID_S})) AS as_of
      FROM visits t WHERE t.width_s <= {MAX_WIDTH_S}),
    moments AS (
      SELECT DISTINCT t.trip_id, t.as_of FROM targets t JOIN firstpub f ON f.trip_id = t.trip_id WHERE f.first_published <= t.as_of),
    known AS (
      SELECT m.trip_id, m.as_of, p.stop_sequence AS last_seq, p.delay_s, p.actual AS last_actual, p.sched AS last_sched, p.visible_at,
             row_number() OVER (PARTITION BY m.trip_id, m.as_of ORDER BY p.stop_sequence DESC) AS rn
      FROM moments m JOIN visits p
        ON p.trip_id = m.trip_id AND p.visible_at <= m.as_of AND p.actual <= m.as_of AND p.width_s <= {STATE_WIDTH_S}),
    state AS (
      SELECT trip_id, as_of,
             max(CASE WHEN rn = 1 THEN last_seq END) AS last_seq, max(CASE WHEN rn = 1 THEN delay_s END) AS delay_now_s,
             max(CASE WHEN rn = 1 THEN last_actual END) AS last_actual, max(CASE WHEN rn = 1 THEN last_sched END) AS last_sched,
             max(CASE WHEN rn = 1 THEN visible_at END) AS state_visible_at, max(CASE WHEN rn = 2 THEN delay_s END) AS delay_prev_s
      FROM known WHERE rn <= 2 GROUP BY trip_id, as_of),
    kcm_all AS (
      SELECT t.trip_id, t.stop_sequence, t.as_of, s.vehicle_id, coalesce(s.trip_schedule_relationship, 0) AS trip_rel,
             CAST(s.arrival_time AS DOUBLE) AS kcm_pred, CAST(s.last_seen AS DOUBLE) AS last_seen, CAST(s.first_seen AS DOUBLE) AS first_seen,
             row_number() OVER (PARTITION BY t.trip_id, t.stop_sequence, t.as_of ORDER BY s.first_seen DESC) AS rn
      FROM targets t JOIN {silver}.trip_update_snapshots s
        ON s.service_date = t.service_date AND s.trip_id = t.trip_id AND s.stop_sequence = t.stop_sequence
      WHERE CAST(s.first_seen AS DOUBLE) <= t.as_of),
    kcm AS (SELECT * FROM kcm_all WHERE rn = 1),
    hist AS (
      SELECT sv.route_id, sv.stop_id, {band_sql(pt_hour)} AS band, {day_type_sql("sv.service_date")} AS day_type,
             avg(sv.arr_delay_s) AS hist_delay_mean_s, count(*) AS hist_n, max(sv.service_date) AS hist_last_day
      FROM {silver}.stop_visits sv
      WHERE sv.service_date BETWEEN DATE'{d}' - INTERVAL {HISTORY_DAYS} DAYS AND DATE'{d}' - INTERVAL 1 DAYS
        AND sv.arr_est IS NOT NULL AND sv.arr_delay_s IS NOT NULL AND unix_timestamp(sv.arr_hi) - unix_timestamp(sv.arr_lo) <= {STATE_WIDTH_S}
      GROUP BY 1, 2, 3, 4),
    wx AS (SELECT hour_start, weather_class FROM {silver}.weather_hourly WHERE n_obs >= 6)
    SELECT t.service_date, t.trip_id, t.route_id, t.direction_id, t.stop_sequence, t.stop_id, t.timepoint,
           timestamp_seconds(t.as_of) AS as_of, timestamp_seconds(t.sched) AS sched_ts, timestamp_seconds(t.actual) AS actual_ts,
           t.delay_s AS lateness_s, t.actual - t.as_of AS eta_s, t.width_s,
           t.sched - t.as_of AS sched_to_go_s, hour(from_utc_timestamp(timestamp_seconds(t.as_of), 'America/Los_Angeles')) AS hour_pt,
           dayofweek(t.service_date) AS dow, {day_type_sql("t.service_date")} AS day_type,
           {band_sql("hour(from_utc_timestamp(timestamp_seconds(t.sched), 'America/Los_Angeles'))")} AS band,
           st.delay_now_s, st.delay_prev_s, st.delay_now_s - st.delay_prev_s AS delay_step_s, st.last_seq,
           t.stop_sequence - st.last_seq AS stops_to_go, t.as_of - st.last_actual AS state_age_s,
           t.as_of - st.state_visible_at AS state_visible_lag_s, t.sched - st.last_sched AS sched_gap_s,
           timestamp_seconds(k.kcm_pred) AS kcm_pred_ts, k.kcm_pred - t.sched AS kcm_delay_s, greatest(0, t.as_of - k.last_seen) AS kcm_age_s,
           (k.vehicle_id IS NOT NULL AND k.vehicle_id <> '') AS kcm_has_bus, (k.trip_rel = 3) AS kcm_canceled,
           h.hist_delay_mean_s, h.hist_n, w.weather_class AS weather_prev_hour,
           timestamp_seconds(st.state_visible_at) AS state_visible_at, timestamp_seconds(k.first_seen) AS kcm_first_seen, h.hist_last_day
    FROM targets t
    JOIN state st ON st.trip_id = t.trip_id AND st.as_of = t.as_of
    LEFT JOIN kcm k ON k.trip_id = t.trip_id AND k.stop_sequence = t.stop_sequence AND k.as_of = t.as_of
    LEFT JOIN hist h ON h.route_id = t.route_id AND h.stop_id = t.stop_id
         AND h.band = {band_sql("hour(from_utc_timestamp(timestamp_seconds(t.sched), 'America/Los_Angeles'))")} AND h.day_type = {day_type_sql("t.service_date")}
    LEFT JOIN wx w ON w.hour_start = date_trunc('HOUR', timestamp_seconds(t.as_of) - INTERVAL 1 HOUR)
    WHERE st.delay_now_s IS NOT NULL AND t.stop_sequence > st.last_seq"""


def build_arrival_examples(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    target = f"workspace.{prefix}_ml.{TABLE}"
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
    for day in sorted(d for d in available if d is not None):
        if day in done and day < recent and (rebuild_from is None or day < rebuild_from):
            continue
        frame = spark.sql(arrival_examples_sql(prefix, day))
        if spark.catalog.tableExists(target):
            spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
        frame.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)
        n = (
            spark.table(target)
            .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
            .count()
        )
        print(f"arrival examples {day}: {n} rows")
