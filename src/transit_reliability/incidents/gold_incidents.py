# ruff: noqa: E501  (SQL is kept on readable lines)
"""Gold incident_routes: collisions and fires (Seattle Fire 911 dispatches) near bus stops.

One row per incident and route with a stop within NEAR_M: the starting point for "was this stretch slow
because of a crash?". Medical calls are left out (they rarely block a road). Last 28 days.
"""

from typing import Any

INCIDENT_ROUTES = "incident_routes"
NEAR_M = 150
PAST_DAYS = 28


def incident_routes_sql(prefix: str) -> str:
    silver = f"workspace.{prefix}_silver"
    dist = "111320 * sqrt(pow((s.stop_lon - i.longitude) * cos(radians(i.latitude)), 2) + pow((s.stop_lat - i.latitude) * 110540 / 111320, 2))"
    return f"""
    WITH fv AS (SELECT max(feed_version) AS v FROM {silver}.gtfs_trips),
    i AS (SELECT * FROM {silver}.incidents WHERE category IN ('traffic', 'fire') AND latitude IS NOT NULL
          AND dispatched_ts >= current_timestamp() - INTERVAL {PAST_DAYS} DAYS),
    stops AS (SELECT stop_id, stop_lat, stop_lon FROM {silver}.gtfs_stops, fv WHERE feed_version = fv.v),
    near AS (SELECT i.incident_number, s.stop_id, {dist} AS metres FROM i JOIN stops s
             ON abs(s.stop_lat - i.latitude) < 0.003 AND abs(s.stop_lon - i.longitude) < 0.004 WHERE {dist} <= {NEAR_M}),
    stop_routes AS (SELECT DISTINCT st.stop_id, t.route_id FROM {silver}.gtfs_stop_times st
             JOIN {silver}.gtfs_trips t ON t.feed_version = st.feed_version AND t.trip_id = st.trip_id, fv WHERE st.feed_version = fv.v),
    routes AS (SELECT route_id, max_by(route_short_name, feed_version) AS route_short_name FROM {silver}.gtfs_routes GROUP BY route_id)
    SELECT i.incident_number, i.type, i.category, i.address, i.dispatched_ts, i.latitude, i.longitude,
           sr.route_id, r.route_short_name, round(min(n.metres)) AS nearest_stop_m
    FROM near n JOIN i ON i.incident_number = n.incident_number JOIN stop_routes sr ON sr.stop_id = n.stop_id
    LEFT JOIN routes r ON r.route_id = sr.route_id
    GROUP BY i.incident_number, i.type, i.category, i.address, i.dispatched_ts, i.latitude, i.longitude, sr.route_id, r.route_short_name"""


def build_incident_routes(spark: Any, prefix: str) -> None:
    if not spark.catalog.tableExists(f"workspace.{prefix}_silver.incidents"):
        print("no silver incidents table yet; nothing to build")
        return
    target = f"workspace.{prefix}_gold.{INCIDENT_ROUTES}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {incident_routes_sql(prefix)}")
    print(f"{target}: {spark.table(target).count()} incident-route rows")
