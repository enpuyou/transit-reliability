# ruff: noqa: E501  (SQL is kept on readable lines)
"""Write the live map's static data files into site/map/:

    uv run python scripts/make_map_data.py --profile enpu

- routes.json: route id -> route number. The live feed only carries the agency's internal id.
- stops.json: stop id -> stop name.
- stop_history.json: how late buses usually reach each timepoint, from our own past days (the last 28
  complete days). Key "route|direction|stop" -> "daytype.band" -> [median seconds late, 85th percentile
  seconds late, number of trips]. Negative means early. Only timepoints (stops with a published time),
  only arrival estimates whose interval is at most 120 s wide, only groups with at least 5 trips.

All of it is public timetable data or aggregates of past days; nothing about today's buses. Needs the
SQL warehouse to be running. Routes and stops change a few times a year; the history should be
refreshed daily (not automated yet, see decision 0025).
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_site_example import make_query  # noqa: E402

S = "workspace.transit_silver"
WINDOW_DAYS = 28
MIN_TRIPS = 5
MAX_WIDTH_S = 120
PT = "from_utc_timestamp(sched_arr, 'America/Los_Angeles')"
BAND = (
    f"CASE WHEN hour({PT}) BETWEEN 6 AND 8 THEN 'am' WHEN hour({PT}) BETWEEN 9 AND 14 THEN 'mid' "
    f"WHEN hour({PT}) BETWEEN 15 AND 18 THEN 'pm' WHEN hour({PT}) BETWEEN 19 AND 23 THEN 'eve' ELSE 'night' END"
)
DAY = "CASE dayofweek(service_date) WHEN 1 THEN 'sun' WHEN 7 THEN 'sat' ELSE 'wk' END"


def history_sql(last_day: dt.date) -> str:
    first = last_day - dt.timedelta(days=WINDOW_DAYS - 1)
    return f"""SELECT route_id, direction_id, stop_id, {DAY} AS d, {BAND} AS b,
        percentile(arr_delay_s, 0.5), percentile(arr_delay_s, 0.85), count(*)
        FROM {S}.stop_visits
        WHERE timepoint = 1 AND arr_est IS NOT NULL AND arr_delay_s IS NOT NULL
          AND unix_timestamp(arr_hi) - unix_timestamp(arr_lo) <= {MAX_WIDTH_S}
          AND service_date BETWEEN DATE'{first.isoformat()}' AND DATE'{last_day.isoformat()}'
        GROUP BY 1, 2, 3, 4, 5 HAVING count(*) >= {MIN_TRIPS}"""


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

    oldest, newest = q(
        f"SELECT min(service_date), max(service_date) FROM {S}.stop_visits WHERE arr_est IS NOT NULL"
    )[0]
    last_day = dt.date.fromisoformat(newest) - dt.timedelta(days=2)  # complete days only
    hist: dict[str, dict[str, list[float | int]]] = {}
    for route, direction, stop, d, b, med, p85, n in q(history_sql(last_day)):
        hist.setdefault(f"{route}|{direction}|{stop}", {})[f"{d}.{b}"] = [
            round(float(med)),
            round(float(p85)),
            int(n),
        ]
    first = max(last_day - dt.timedelta(days=WINDOW_DAYS - 1), dt.date.fromisoformat(oldest))
    write(
        out / "stop_history.json",
        {"first_day": first.isoformat(), "last_day": last_day.isoformat(), "h": hist},
    )


if __name__ == "__main__":
    main()
