# ruff: noqa: E501  (SQL is kept on readable lines)
"""Write the live map's static data files into site/map/:

    uv run python scripts/make_map_data.py --profile enpu

- routes.json: route id -> route number. The live feed only carries the agency's internal id.
- stops.json: stop id -> stop name.
- shapes.json: the route lines. Key "route|direction" -> list of lines (each a list of [lon, lat]):
  the shapes that at least 20% of that route direction's trips use, simplified to within about 8 m.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_site_example import make_query  # noqa: E402

from transit_reliability.public.map_data import (  # noqa: E402
    COMPLETE_AFTER_DAYS,
    WINDOW_DAYS,
    history_payload,
    history_sql,
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


if __name__ == "__main__":
    main()
