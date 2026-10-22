# ruff: noqa: E501  (SQL is kept on readable lines)
"""Gold event_routes: which bus routes pass near each upcoming or recent event.

One row per event and route: the event, its venue, and how close the route's nearest stop is to the
venue (within NEAR_M). Built from the newest timetable. This is what the map shows ("events near this
route") and what an analysis of event delays will start from, once there are weeks of data.
"""

from typing import Any

EVENT_ROUTES = "event_routes"
NEAR_M = 400
PAST_DAYS = 14  # keep recent past events too, so delays can be compared afterwards


def event_routes_sql(prefix: str) -> str:
    silver = f"workspace.{prefix}_silver"
    dist = "111320 * sqrt(pow((s.stop_lon - e.longitude) * cos(radians(e.latitude)), 2) + pow((s.stop_lat - e.latitude) * 110540 / 111320, 2))"
    return f"""
    WITH fv AS (SELECT max(feed_version) AS v FROM {silver}.gtfs_trips),
    ev AS (SELECT * FROM {silver}.events WHERE latitude IS NOT NULL AND start_ts >= current_timestamp() - INTERVAL {PAST_DAYS} DAYS),
    stops AS (SELECT stop_id, stop_lat, stop_lon FROM {silver}.gtfs_stops, fv WHERE feed_version = fv.v),
    near AS (SELECT e.event_id, s.stop_id, {dist} AS metres FROM ev e JOIN stops s
             ON abs(s.stop_lat - e.latitude) < 0.006 AND abs(s.stop_lon - e.longitude) < 0.009 WHERE {dist} <= {NEAR_M}),
    stop_routes AS (SELECT DISTINCT st.stop_id, t.route_id FROM {silver}.gtfs_stop_times st
             JOIN {silver}.gtfs_trips t ON t.feed_version = st.feed_version AND t.trip_id = st.trip_id, fv WHERE st.feed_version = fv.v),
    routes AS (SELECT route_id, max_by(route_short_name, feed_version) AS route_short_name FROM {silver}.gtfs_routes GROUP BY route_id)
    SELECT e.event_id, e.name, e.segment, e.start_ts, e.status, e.venue_name, e.latitude, e.longitude, e.large_venue, e.venue_seats,
           sr.route_id, r.route_short_name, round(min(n.metres)) AS nearest_stop_m
    FROM near n JOIN ev e ON e.event_id = n.event_id JOIN stop_routes sr ON sr.stop_id = n.stop_id
    LEFT JOIN routes r ON r.route_id = sr.route_id
    GROUP BY e.event_id, e.name, e.segment, e.start_ts, e.status, e.venue_name, e.latitude, e.longitude, e.large_venue, e.venue_seats, sr.route_id, r.route_short_name"""


def build_event_routes(spark: Any, prefix: str) -> None:
    if not spark.catalog.tableExists(f"workspace.{prefix}_silver.events"):
        print("no silver events table yet; nothing to build")
        return
    target = f"workspace.{prefix}_gold.{EVENT_ROUTES}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {event_routes_sql(prefix)}")
    print(f"{target}: {spark.table(target).count()} event-route rows")
