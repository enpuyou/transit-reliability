# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Gold weather impact: does reliability differ between dry, foggy and rainy hours? (decision 0024)

`weather_impact`: grain (weather class, day type, time band). Each trip is matched to the weather in the
Seattle hour it was scheduled to start (`trip_performance` joined to `weather_hourly`), each segment run to
the hour it departed. Counts are summed, never averaged shares, so any share is recomputed with its sample
size. Only hours with at least `MIN_OBS` observations and trips starting from 4 am on are used (after
midnight a trip's service date and Seattle date differ). Weather and reliability both vary with the time of
day, so rows are compared within the same day type and band; with few rainy hours the comparison is weak
and the website says so. This shows association, not cause.
"""

from typing import Any

from transit_reliability.gtfs_static.gold_segments import band_sql, day_type_sql

TABLE = "weather_impact"
MIN_OBS = 6  # an hour needs this many observations (three stations, two each) to be used
FIRST_HOUR = 4


def weather_impact_sql(prefix: str) -> str:
    silver, gold = f"workspace.{prefix}_silver", f"workspace.{prefix}_gold"
    start_hour = "hour(from_utc_timestamp(t.scheduled_start, 'America/Los_Angeles'))"
    start_date = "to_date(from_utc_timestamp(t.scheduled_start, 'America/Los_Angeles'))"
    return f"""
    WITH w AS (SELECT pt_date, pt_hour, weather_class FROM {silver}.weather_hourly WHERE n_obs >= {MIN_OBS}),
    trips AS (
      SELECT w.weather_class, {day_type_sql("sd")} AS day_type, {band_sql("sh")} AS band, sd, sh,
             t.n_rider_early, t.n_rider_on_time, t.n_rider_late, t.n_rider_uncertain
      FROM (SELECT t.*, {start_date} AS sd, {start_hour} AS sh FROM {gold}.trip_performance t) t
      JOIN w ON w.pt_date = t.sd AND w.pt_hour = t.sh WHERE t.sh >= {FIRST_HOUR}),
    trip_agg AS (
      SELECT weather_class, day_type, band, count(DISTINCT sd, sh) AS n_hours, count(*) AS n_trips,
             sum(n_rider_early + n_rider_on_time + n_rider_late + n_rider_uncertain) AS n_scored,
             sum(n_rider_early) AS n_early, sum(n_rider_on_time) AS n_on_time, sum(n_rider_late) AS n_late, sum(n_rider_uncertain) AS n_uncertain
      FROM trips GROUP BY weather_class, day_type, band),
    seg_agg AS (
      SELECT w.weather_class, s.day_type, s.band, count(*) AS n_runs,
             percentile(s.obs_s - s.sched_s, 0.5) AS median_excess_s,
             avg(CASE WHEN s.obs_s > s.sched_s THEN 1.0 ELSE 0.0 END) AS share_over_schedule
      FROM {gold}.segment_runs s JOIN w ON w.pt_date = s.service_date AND w.pt_hour = s.start_hour_pt
      WHERE s.start_hour_pt >= {FIRST_HOUR} GROUP BY w.weather_class, s.day_type, s.band)
    SELECT a.weather_class, a.day_type, a.band, a.n_hours, a.n_trips, a.n_scored, a.n_early, a.n_on_time, a.n_late, a.n_uncertain,
           s.n_runs, s.median_excess_s, s.share_over_schedule
    FROM trip_agg a LEFT JOIN seg_agg s ON s.weather_class = a.weather_class AND s.day_type = a.day_type AND s.band = a.band"""


def build_weather_impact(spark: Any, prefix: str) -> None:
    target = f"workspace.{prefix}_gold.{TABLE}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {weather_impact_sql(prefix)}")
    print(f"weather impact: {spark.table(target).count()} rows")
