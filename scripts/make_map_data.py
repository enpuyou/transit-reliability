# ruff: noqa: E501  (SQL is kept on readable lines)
"""Write the live map's static data files into site/map/:

    uv run python scripts/make_map_data.py --profile enpu

- routes.json: route id -> route number. The live feed only carries the agency's internal id.
- stops.json: stop id -> stop name.
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
    last_day = dt.date.fromisoformat(newest) - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    first = max(last_day - dt.timedelta(days=WINDOW_DAYS - 1), dt.date.fromisoformat(oldest))
    rows = [tuple(r) for r in q(history_sql("transit", first, last_day))]
    # the daily silver job writes this same file into the state volume; this copy is for local preview
    local = Path("site/data")
    local.mkdir(parents=True, exist_ok=True)
    write(local / "stop_history.json", history_payload(rows, first, last_day))


if __name__ == "__main__":
    main()
