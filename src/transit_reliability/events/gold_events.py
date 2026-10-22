# ruff: noqa: E501  (SQL is kept on readable lines)
"""Gold tables for events.

- `venue_routes`: for each venue, every bus route with a stop within NEAR_M, and that stop's distance.
  Worked out once per venue (not per event) from the newest timetable.
- `event_routes`: each upcoming or recent event with the routes near its venue (events x venue_routes).
  This is what the map shows ("events near this route").
- `event_impact`: for past events, how long buses took between timepoints near the venue in the two hours
  before the start and two hours from two hours after the start (a typical event length; Ticketmaster rarely
  gives an end time), against the same route segments, day type and hours on days with no event at that
  venue in the last 28 days. Meaningful once there are a few weeks of events; until then the counts are small
  and the table says so (n_event_runs, n_base_runs).
"""

from typing import Any

VENUE_ROUTES = "venue_routes"
EVENT_ROUTES = "event_routes"
EVENT_IMPACT = "event_impact"
NEAR_M = 400
PAST_DAYS = 28
DIST = "111320 * sqrt(pow((s.stop_lon - v.longitude) * cos(radians(v.latitude)), 2) + pow((s.stop_lat - v.latitude) * 110540 / 111320, 2))"


def venue_routes_sql(prefix: str) -> str:
    silver = f"workspace.{prefix}_silver"
    return f"""
    WITH fv AS (SELECT max(feed_version) AS v FROM {silver}.gtfs_trips),
    v AS (SELECT * FROM {silver}.venues WHERE latitude IS NOT NULL),
    stops AS (SELECT stop_id, stop_lat, stop_lon FROM {silver}.gtfs_stops, fv WHERE feed_version = fv.v),
    near AS (SELECT v.venue_id, s.stop_id, {DIST} AS metres FROM v JOIN stops s
             ON abs(s.stop_lat - v.latitude) < 0.006 AND abs(s.stop_lon - v.longitude) < 0.009 WHERE {DIST} <= {NEAR_M}),
    stop_routes AS (SELECT DISTINCT st.stop_id, t.route_id FROM {silver}.gtfs_stop_times st
             JOIN {silver}.gtfs_trips t ON t.feed_version = st.feed_version AND t.trip_id = st.trip_id, fv WHERE st.feed_version = fv.v),
    routes AS (SELECT route_id, max_by(route_short_name, feed_version) AS route_short_name FROM {silver}.gtfs_routes GROUP BY route_id)
    SELECT n.venue_id, sr.route_id, r.route_short_name, round(min(n.metres)) AS nearest_stop_m,
           collect_set(n.stop_id) AS stop_ids
    FROM near n JOIN stop_routes sr ON sr.stop_id = n.stop_id LEFT JOIN routes r ON r.route_id = sr.route_id
    GROUP BY n.venue_id, sr.route_id, r.route_short_name"""


def event_routes_sql(prefix: str) -> str:
    silver, gold = f"workspace.{prefix}_silver", f"workspace.{prefix}_gold"
    return f"""
    SELECT e.event_id, e.name, e.segment, e.start_ts, e.status, e.venue_id, e.venue_name, e.latitude, e.longitude,
           e.large_venue, e.venue_seats, vr.route_id, vr.route_short_name, vr.nearest_stop_m
    FROM {silver}.events e JOIN {gold}.{VENUE_ROUTES} vr ON vr.venue_id = e.venue_id
    WHERE e.start_ts >= current_timestamp() - INTERVAL {PAST_DAYS} DAYS"""


def event_impact_sql(prefix: str) -> str:
    silver, gold = f"workspace.{prefix}_silver", f"workspace.{prefix}_gold"
    pt = "from_utc_timestamp(e.start_ts, 'America/Los_Angeles')"
    return f"""
    WITH ev AS (
      SELECT e.event_id, e.name, e.venue_id, e.venue_name, e.large_venue, e.start_ts, to_date({pt}) AS local_date, hour({pt}) AS start_hour
      FROM {silver}.events e
      WHERE e.start_ts BETWEEN current_timestamp() - INTERVAL {PAST_DAYS} DAYS AND current_timestamp() - INTERVAL 6 HOURS
        AND coalesce(e.status, '') NOT IN ('cancelled', 'postponed')),
    windows AS (
      SELECT ev.*, wx.w.name AS window, hx.hour AS hour
      FROM ev LATERAL VIEW explode(array(named_struct('name', 'before', 'h', array(ev.start_hour - 2, ev.start_hour - 1)),
                                         named_struct('name', 'after', 'h', array(ev.start_hour + 2, ev.start_hour + 3)))) wx AS w
      LATERAL VIEW explode(w.h) hx AS hour),
    near_stops AS (SELECT DISTINCT venue_id, s AS stop_id FROM {gold}.{VENUE_ROUTES} LATERAL VIEW explode(stop_ids) x AS s),
    runs AS (
      SELECT r.*, ns.venue_id, r.obs_s - r.sched_s AS excess_s FROM {gold}.segment_runs r
      JOIN near_stops ns ON ns.stop_id = r.from_stop_id OR ns.stop_id = r.to_stop_id
      WHERE r.service_date >= current_date() - INTERVAL {PAST_DAYS + 2} DAYS AND r.obs_width_s <= 180),
    event_days AS (SELECT DISTINCT venue_id, local_date FROM ev),
    on_event AS (
      SELECT w.event_id, w.window, percentile(r.excess_s, 0.5) AS median_excess_s, count(*) AS n_event_runs,
             first(r.day_type) AS day_type
      FROM windows w JOIN runs r ON r.venue_id = w.venue_id AND r.service_date = w.local_date AND r.start_hour_pt = w.hour
      GROUP BY w.event_id, w.window),
    baseline AS (
      SELECT w.event_id, w.window, percentile(r.excess_s, 0.5) AS base_median_excess_s, count(*) AS n_base_runs
      FROM windows w JOIN runs r ON r.venue_id = w.venue_id AND r.start_hour_pt = w.hour
        AND r.day_type = CASE dayofweek(w.local_date) WHEN 1 THEN 'sunday' WHEN 7 THEN 'saturday' ELSE 'weekday' END
      LEFT JOIN event_days d ON d.venue_id = r.venue_id AND d.local_date = r.service_date
      WHERE d.local_date IS NULL
      GROUP BY w.event_id, w.window)
    SELECT ev.event_id, ev.name, ev.venue_name, ev.large_venue, ev.start_ts, o.window,
           o.n_event_runs, o.median_excess_s, b.n_base_runs, b.base_median_excess_s,
           o.median_excess_s - b.base_median_excess_s AS extra_s
    FROM ev JOIN on_event o ON o.event_id = ev.event_id LEFT JOIN baseline b ON b.event_id = o.event_id AND b.window = o.window"""


def build_event_routes(spark: Any, prefix: str) -> None:
    if not spark.catalog.tableExists(f"workspace.{prefix}_silver.venues"):
        print("no silver venues table yet; nothing to build")
        return
    gold = f"workspace.{prefix}_gold"
    for table, sql in (
        (VENUE_ROUTES, venue_routes_sql(prefix)),
        (EVENT_ROUTES, event_routes_sql(prefix)),
    ):
        spark.sql(f"CREATE OR REPLACE TABLE {gold}.{table} AS {sql}")
        print(f"{gold}.{table}: {spark.table(f'{gold}.{table}').count()} rows")
    if spark.catalog.tableExists(f"{gold}.segment_runs"):
        spark.sql(f"CREATE OR REPLACE TABLE {gold}.{EVENT_IMPACT} AS {event_impact_sql(prefix)}")
        print(f"{gold}.{EVENT_IMPACT}: {spark.table(f'{gold}.{EVENT_IMPACT}').count()} rows")
