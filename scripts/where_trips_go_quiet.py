# ruff: noqa: E501  (SQL is kept on readable lines)
"""Where and when does a trip's last bus report come, compared with its final stop and its scheduled end?

    uv run python scripts/where_trips_go_quiet.py

Finding (2026-10-20): the agency's feed stops showing a trip when its scheduled time runs out, not when the bus
arrives; see decision 0025.
"""

import sys

sys.path.insert(0, "scripts")  # noqa: E702
from make_site_example import make_query

q = make_query("enpu")
S = "workspace.transit_silver"
DAY = "2026-10-20"
sql = f"""
WITH fv AS (SELECT max(feed_version) v FROM {S}.gtfs_trips),
t AS (SELECT st.trip_id, st.route_id, g.block_id, g.service_id FROM {S}.scheduled_trips st
      JOIN {S}.gtfs_trips g ON g.feed_version = st.feed_version AND g.trip_id = st.trip_id WHERE st.service_date = DATE'{DAY}'),
fl AS (SELECT trip_id, max_by(stop_id, stop_sequence) last_stop, unix_timestamp(min_by(scheduled_departure_ts, stop_sequence)) first_dep,
         unix_timestamp(max_by(scheduled_arrival_ts, stop_sequence)) last_arr
       FROM {S}.scheduled_stop_times WHERE service_date = DATE'{DAY}' GROUP BY trip_id),
veh AS (SELECT trip_id, max(unix_timestamp(vehicle_ts)) last_ts, max_by(latitude, vehicle_ts) lat, max_by(longitude, vehicle_ts) lon
        FROM {S}.vehicle_positions WHERE service_date = DATE'{DAY}' AND trip_id IS NOT NULL GROUP BY trip_id),
j AS (SELECT t.trip_id, fl.last_stop, fl.last_arr, veh.last_ts, veh.lat, veh.lon,
        lead(t.trip_id) OVER w AS next_trip
      FROM t JOIN fl ON fl.trip_id = t.trip_id LEFT JOIN veh ON veh.trip_id = t.trip_id
      WINDOW w AS (PARTITION BY t.block_id, t.service_id ORDER BY fl.first_dep)),
k AS (SELECT j.*, CASE WHEN j.next_trip IS NULL THEN 'last trip of the bus day'
                       WHEN nv.trip_id IS NULL THEN 'next trip never ran'
                       ELSE 'next trip ran' END AS kind,
        111320 * sqrt(pow((j.lon - s.stop_lon) * cos(radians(s.stop_lat)), 2) + pow((j.lat - s.stop_lat) * 110540 / 111320, 2)) AS gap_m,
        (j.last_ts - j.last_arr) / 60.0 AS vs_sched_end_min
      FROM j LEFT JOIN veh nv ON nv.trip_id = j.next_trip
      JOIN {S}.gtfs_stops s ON s.stop_id = j.last_stop AND s.feed_version = (SELECT v FROM fv)
      WHERE j.last_ts IS NOT NULL)
SELECT kind, count(*), percentile(gap_m, array(0.25, 0.5, 0.75, 0.9)), avg(CASE WHEN gap_m <= 250 THEN 1.0 ELSE 0.0 END),
       avg(CASE WHEN gap_m <= 1500 THEN 1.0 ELSE 0.0 END), avg(CASE WHEN gap_m > 3000 THEN 1.0 ELSE 0.0 END),
       percentile(vs_sched_end_min, array(0.1, 0.5, 0.9))
FROM k GROUP BY kind ORDER BY kind"""
for r in q(sql):
    import json

    g = [round(float(x)) for x in json.loads(r[2])]
    t = [round(float(x), 1) for x in json.loads(r[6])]
    print(
        f"{r[0]:28} trips {int(r[1]):>6} | last report from final stop, metres p25/p50/p75/p90: {g} | within 250 m {float(r[3]):.0%}, within 1.5 km {float(r[4]):.0%}, over 3 km {float(r[5]):.0%} | last report vs scheduled end, min p10/p50/p90: {t}"
    )
