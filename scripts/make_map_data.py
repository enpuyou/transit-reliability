# ruff: noqa: E501  (SQL is kept on readable lines)
"""Write the live map's static data files into site/map/:

    uv run python scripts/make_map_data.py --profile enpu

- routes.json: route id -> route number. The live feed only carries the agency's internal id.
- stops.json: stop id -> stop name.
- headsigns.json: "route|direction" -> the destination sign text the trips of that direction show most
  often (for the direction picker, e.g. "Downtown Seattle").
- route_stops.json: "route|direction" -> the stops of that route direction, each [stop id, lon, lat,
  1 if it has a timetable time else 0], in trip order. Taken from one representative trip for each of the
  main patterns (the same ones shapes.json draws), so a branching route lists the stops of every branch.
- shapes.json: the route lines. Key "route|direction" -> list of lines (each a list of [lon, lat]):
  the shapes that at least 20% of that route direction's trips use, simplified to within about 8 m.
- trips/<route id>.json: for each trip of the route in the newest timetable: its direction, its timetable
  ("stop:seconds after midnight of the service day" per stop), and the trip the same bus runs just before it
  (the schedule's block: same block and service pattern, the trip that starts last before this one), with that
  trip's route, last stop and scheduled end; plus the coordinates of every stop the route's trips use. Used for
  the "timetable pace" estimate and to say whether a bus is waiting at a trip's first stop.
- site/data/stop_history.json (local preview only): typical lateness per timetable stop. The daily
  silver job builds the real one (`transit_reliability.public.map_data`); the site workflow copies it.

All of it is public timetable data or aggregates of past days; nothing about today's buses. Needs the
SQL warehouse to be running. Routes and stops change a few times a year.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_site_example import make_query  # noqa: E402

from transit_reliability.public.map_data import (  # noqa: E402
    COMPLETE_AFTER_DAYS,
    WINDOW_DAYS,
    history_payload,
    history_sql,
    trip_history_payloads,
    trip_history_sql,
)

S = "workspace.transit_silver"


SHAPE_MIN_SHARE = 0.2  # a route direction's patterns used by at least this share of its trips
SIMPLIFY_M = 8.0


def simplify(points: list[tuple[float, float]], tolerance_m: float) -> list[tuple[float, float]]:
    """Douglas-Peucker: drop points that lie within `tolerance_m` of the line between kept ones."""

    import math

    if len(points) < 3:
        return points
    lat0 = points[0][1]
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    xy = [(lon * kx, lat * ky) for lon, lat in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        far, far_d = -1, 0.0
        for i in range(a + 1, b):
            px, py = xy[i]
            d = (
                math.hypot(px - ax, py - ay)
                if length == 0
                else abs(dx * (ay - py) - (ax - px) * dy) / length
            )
            if d > far_d:
                far, far_d = i, d
        if far_d > tolerance_m:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def shapes_sql() -> str:
    return f"""WITH fv AS (SELECT max(feed_version) AS v FROM {S}.gtfs_shapes),
      t AS (SELECT route_id, direction_id, shape_id, count(*) AS n FROM {S}.gtfs_trips, fv
            WHERE feed_version = fv.v GROUP BY 1, 2, 3),
      r AS (SELECT *, n / sum(n) OVER (PARTITION BY route_id, direction_id) AS share FROM t)
      SELECT r.route_id, r.direction_id, r.shape_id, s.shape_pt_lat, s.shape_pt_lon
      FROM r JOIN {S}.gtfs_shapes s ON s.shape_id = r.shape_id, fv
      WHERE s.feed_version = fv.v AND r.share >= {SHAPE_MIN_SHARE}
      ORDER BY r.route_id, r.direction_id, r.shape_id, s.shape_pt_sequence"""


def route_stops_sql() -> str:
    return f"""WITH fv AS (SELECT max(feed_version) AS v FROM {S}.gtfs_shapes),
      t AS (SELECT route_id, direction_id, shape_id, count(*) AS n, min(trip_id) AS trip_id
            FROM {S}.gtfs_trips, fv WHERE feed_version = fv.v GROUP BY 1, 2, 3),
      r AS (SELECT *, n / sum(n) OVER (PARTITION BY route_id, direction_id) AS share FROM t)
      SELECT r.route_id, r.direction_id, r.shape_id, st.stop_sequence, st.stop_id, p.stop_lat, p.stop_lon,
             coalesce(st.timepoint, 0)
      FROM r JOIN {S}.gtfs_stop_times st ON st.trip_id = r.trip_id
      JOIN {S}.gtfs_stops p ON p.stop_id = st.stop_id AND p.feed_version = st.feed_version, fv
      WHERE st.feed_version = fv.v AND r.share >= {SHAPE_MIN_SHARE}
      ORDER BY r.route_id, r.direction_id, r.shape_id, st.stop_sequence"""


def trips_sql() -> str:
    return f"""WITH fv AS (SELECT max(feed_version) AS v FROM {S}.gtfs_trips),
      t AS (SELECT trip_id, route_id, direction_id, service_id, block_id FROM {S}.gtfs_trips, fv WHERE feed_version = fv.v),
      st AS (SELECT trip_id, stop_sequence, stop_id, arrival_seconds FROM {S}.gtfs_stop_times, fv WHERE feed_version = fv.v),
      per AS (SELECT trip_id, min_by(arrival_seconds, stop_sequence) AS start_s, max_by(arrival_seconds, stop_sequence) AS end_s,
                max_by(stop_id, stop_sequence) AS last_stop,
                concat_ws(';', transform(sort_array(collect_list(struct(stop_sequence, stop_id, arrival_seconds))), x -> concat(x.stop_id, ':', x.arrival_seconds))) AS times
              FROM st GROUP BY trip_id),
      j AS (SELECT t.*, per.start_s, per.end_s, per.last_stop, per.times,
              lag(t.trip_id) OVER w AS prev_trip, lag(t.route_id) OVER w AS prev_route,
              lag(per.last_stop) OVER w AS prev_last_stop, lag(per.end_s) OVER w AS prev_end_s
            FROM t JOIN per ON per.trip_id = t.trip_id
            WINDOW w AS (PARTITION BY t.block_id, t.service_id ORDER BY per.start_s))
      SELECT route_id, trip_id, direction_id, prev_trip, prev_route, prev_last_stop, prev_end_s, times FROM j"""


def write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
    print(f"wrote {path}: {path.stat().st_size:,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out-dir", default="site/map")
    args = parser.parse_args()
    q = make_query(args.profile)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    routes = q(
        f"SELECT route_id, max_by(coalesce(route_short_name, route_long_name), feed_version) FROM {S}.gtfs_routes GROUP BY route_id"
    )
    write(out / "routes.json", {str(r[0]): str(r[1]) for r in routes if r[1]})

    stops = q(
        f"SELECT stop_id, max_by(stop_name, feed_version) FROM {S}.gtfs_stops GROUP BY stop_id"
    )
    write(out / "stops.json", {str(r[0]): str(r[1]) for r in stops if r[1]})

    heads = q(
        f"""WITH fv AS (SELECT max(feed_version) AS v FROM {S}.gtfs_shapes),
        t AS (SELECT route_id, direction_id, trip_headsign, count(*) AS n FROM {S}.gtfs_trips, fv
              WHERE feed_version = fv.v AND trip_headsign IS NOT NULL GROUP BY 1, 2, 3)
        SELECT route_id, direction_id, max_by(trip_headsign, n) FROM t GROUP BY 1, 2"""
    )
    write(out / "headsigns.json", {f"{r[0]}|{r[1]}": str(r[2]) for r in heads})

    route_stops: dict[str, list[list[object]]] = {}
    seen: dict[str, set[str]] = {}
    for route, direction, _shape, _seq, stop, lat, lon, tp in q(route_stops_sql()):
        key = f"{route}|{direction}"
        if stop in seen.setdefault(key, set()):
            continue  # a stop shared by two patterns of the same route direction is listed once
        seen[key].add(stop)
        route_stops.setdefault(key, []).append(
            [str(stop), round(float(lon), 5), round(float(lat), 5), int(tp)]
        )
    write(out / "route_stops.json", route_stops)

    coords = {
        str(r[0]): [round(float(r[2]), 5), round(float(r[1]), 5)]
        for r in q(
            f"SELECT stop_id, max_by(stop_lat, feed_version), max_by(stop_lon, feed_version) FROM {S}.gtfs_stops GROUP BY stop_id"
        )
    }
    per_route: dict[str, dict[str, Any]] = {}
    for route, trip, direction, prev, prev_route, prev_last, prev_end, times in q(trips_sql()):
        r = per_route.setdefault(str(route), {"stops": {}, "trips": {}})
        r["trips"][str(trip)] = [
            int(direction),
            prev,
            prev_route,
            prev_last,
            None if prev_end is None else int(prev_end),
            times,
        ]
        for part in (times or "").split(";"):
            sid = part.split(":")[0]
            if sid in coords:
                r["stops"][sid] = coords[sid]
        if prev_last in coords:
            r["stops"][prev_last] = coords[prev_last]
    (out / "trips").mkdir(exist_ok=True)
    total = 0
    for route, data in per_route.items():
        path = out / "trips" / f"{route}.json"
        path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
        total += path.stat().st_size
    print(f"wrote {len(per_route)} files in {out / 'trips'}: {total:,} bytes in total")

    lines: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    for route, direction, shape, lat, lon in q(shapes_sql()):
        lines.setdefault((str(route), str(direction), str(shape)), []).append(
            (float(lon), float(lat))
        )
    shapes: dict[str, list[list[list[float]]]] = {}
    for (route, direction, _shape), pts in lines.items():
        simple = [[round(x, 5), round(y, 5)] for x, y in simplify(pts, SIMPLIFY_M)]
        shapes.setdefault(f"{route}|{direction}", []).append(simple)
    write(out / "shapes.json", shapes)

    oldest, newest = q(
        f"SELECT min(service_date), max(service_date) FROM {S}.stop_visits WHERE arr_est IS NOT NULL"
    )[0]
    last_day = dt.date.fromisoformat(newest) - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    first = max(last_day - dt.timedelta(days=WINDOW_DAYS - 1), dt.date.fromisoformat(oldest))
    rows = [tuple(r) for r in q(history_sql("transit", first, last_day))]
    # the daily silver job writes this same file into the state volume; this copy is for local preview
    local = Path("site/data")
    local.mkdir(parents=True, exist_ok=True)
    write(local / "stop_history.json", history_payload(rows, first, last_day))
    # per-trip history, one file per route (the daily job writes the same into the state volume)
    trips_rows = [tuple(r) for r in q(trip_history_sql("transit", first, last_day))]
    (local / "trip_history").mkdir(exist_ok=True)
    for route, payload in trip_history_payloads(trips_rows, first, last_day).items():
        (local / "trip_history" / f"{route}.json").write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True)
        )
    print(f"wrote site/data/trip_history/: {len(trips_rows)} trip-stop rows")


if __name__ == "__main__":
    main()
