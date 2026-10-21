"""Write site/map/routes.json: route id -> route number, so the live map can say "Route 49".

    uv run python scripts/make_map_data.py --profile enpu

The live bus feed only carries the agency's internal route id (like 100001). The timetable (silver
`gtfs_routes`) says which route number that is. This is public timetable data.
Needs the SQL warehouse to be running. Re-run when the agency adds routes (a few times a year).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_site_example import make_query  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out", default="site/map/routes.json")
    args = parser.parse_args()
    rows = make_query(args.profile)(
        """SELECT route_id, max_by(coalesce(route_short_name, route_long_name), feed_version)
        FROM workspace.transit_silver.gtfs_routes GROUP BY route_id ORDER BY route_id"""
    )
    routes = {str(r[0]): str(r[1]) for r in rows if r[1]}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(routes, separators=(",", ":"), sort_keys=True))
    print(f"wrote {out}: {len(routes)} routes, e.g. {list(routes.items())[:3]}")


if __name__ == "__main__":
    main()
