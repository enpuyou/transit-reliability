# ruff: noqa: E501  (SQL is kept on readable lines)
"""Write site/example.json: one real trip worked through step by step, for the website's explainer.

    uv run python scripts/make_site_example.py --profile enpu

It pulls one trip's route line, stops and GPS reports from Databricks and runs the SAME functions the
pipeline uses (`arrivals/infer.py`), so every number on the page is what the system computes. The file
holds no vehicle id; positions appear only as distances along the route, plus the route line's own
coordinates (public timetable data). Needs the SQL warehouse to be running.
"""

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_reliability.arrivals.infer import (  # noqa: E402
    ARRIVE_TOL_FT,
    BACK_TOL_FT,
    DEPART_TOL_FT,
    FEET_PER_DEGREE_LAT,
    FORWARD_SLACK_FT,
    MAX_SPEED_FT_S,
    Fix,
    Shape,
    StopTarget,
    _project_track,
    infer_stop_visits,
)

S = "workspace.transit_silver"


def api(profile: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    cmd = ["databricks", "api", method, path, "--profile", profile]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode or not done.stdout:
        raise RuntimeError(done.stderr.strip()[:300] or "no response (is the warehouse running?)")
    return json.loads(done.stdout)


def make_query(profile: str) -> Any:
    warehouse = json.loads(
        subprocess.run(
            ["databricks", "warehouses", "list", "-o", "json", "--profile", profile],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]["id"]

    def query(sql: str) -> list[list[Any]]:
        d = api(
            profile,
            "post",
            "/api/2.0/sql/statements",
            {"warehouse_id": warehouse, "statement": sql, "wait_timeout": "50s"},
        )
        while d["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(4)
            d = api(profile, "get", "/api/2.0/sql/statements/" + d["statement_id"])
        if d["status"]["state"] != "SUCCEEDED":
            raise RuntimeError(json.dumps(d["status"])[:300])
        rows = list(d.get("result", {}).get("data_array", []))
        chunk = d.get("result", {}).get("next_chunk_index")
        while chunk is not None:
            page = api(
                profile, "get", f"/api/2.0/sql/statements/{d['statement_id']}/result/chunks/{chunk}"
            )
            rows += page.get("data_array", [])
            chunk = page.get("next_chunk_index")
        total = d["manifest"].get("total_row_count")
        if total is not None and len(rows) != total:
            raise RuntimeError(f"got {len(rows)} rows, expected {total}")
        return rows

    return query


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--day", default="2026-10-16")
    parser.add_argument("--trip", default="801199891", help="route 49 trip used on the page")
    parser.add_argument("--out", default="site/example.json")
    args = parser.parse_args()
    q = make_query(args.profile)
    day, trip = args.day, args.trip

    route_name, headsign, shape_id = q(
        f"""SELECT r.route_short_name, t.trip_headsign, t.shape_id FROM {S}.gtfs_trips t
        JOIN {S}.scheduled_trips st ON st.feed_version = t.feed_version AND st.trip_id = t.trip_id
        JOIN {S}.gtfs_routes r ON r.feed_version = t.feed_version AND r.route_id = t.route_id
        WHERE st.service_date = DATE'{day}' AND t.trip_id = '{trip}' LIMIT 1"""
    )[0]
    pts = [
        (float(a), float(b), float(c))
        for a, b, c in q(
            f"""SELECT shape_pt_lat, shape_pt_lon, shape_dist_traveled FROM {S}.gtfs_shapes
            WHERE shape_id = '{shape_id}' AND feed_version = (SELECT max(feed_version) FROM {S}.gtfs_shapes)
            ORDER BY shape_pt_sequence"""
        )
    ]
    stops = q(
        f"""SELECT sst.stop_sequence, sst.stop_id, st.shape_dist_traveled, unix_timestamp(sst.scheduled_arrival_ts),
        unix_timestamp(sst.scheduled_departure_ts), sst.timepoint, s.stop_name FROM {S}.scheduled_stop_times sst
        JOIN {S}.gtfs_stop_times st ON st.feed_version = sst.feed_version AND st.trip_id = sst.trip_id AND st.stop_sequence = sst.stop_sequence
        JOIN {S}.gtfs_stops s ON s.feed_version = sst.feed_version AND s.stop_id = sst.stop_id
        WHERE sst.service_date = DATE'{day}' AND sst.trip_id = '{trip}' ORDER BY sst.stop_sequence"""
    )
    fixes = [
        Fix(
            float(r[0]),
            float(r[1]),
            float(r[2]),
            None if r[3] is None else int(r[3]),
            r[4] == "STOPPED_AT",
        )
        for r in q(
            f"""SELECT unix_timestamp(vehicle_ts), latitude, longitude, current_stop_sequence, current_status
            FROM {S}.vehicle_positions WHERE service_date = DATE'{day}' AND trip_id = '{trip}' ORDER BY vehicle_ts"""
        )
    ]
    shape = Shape(pts)
    results = infer_stop_visits(
        shape, fixes, [StopTarget(int(s[0]), str(s[1]), float(s[2])) for s in stops]
    )
    track = dict(_project_track(shape, sorted(fixes, key=lambda f: f.ts)))

    # a middle timepoint where both arrival and departure were interpolated
    candidates = [
        (s, v)
        for s, v in zip(stops, results, strict=True)
        if s[5] == "1"
        and v.arr_method == "interpolated"
        and v.dep_method == "interpolated"
        and 8 <= int(s[0]) <= len(stops) - 8
    ]
    stop, visit = candidates[len(candidates) // 2]
    stop_d = float(stop[2])
    near_fixes = [
        f
        for f in sorted(fixes, key=lambda f: f.ts)
        if f.ts in track and abs(track[f.ts] - stop_d) <= 1100
    ]

    def crossing(threshold: float) -> dict[str, Any]:
        before = [f for f in near_fixes if track[f.ts] < threshold][-1]
        after = [f for f in near_fixes if track[f.ts] >= threshold][0]
        frac = (threshold - track[before.ts]) / (track[after.ts] - track[before.ts])
        return {
            "threshold_ft": threshold,
            "before": {"ts": int(before.ts), "dist_ft": round(track[before.ts], 1)},
            "after": {"ts": int(after.ts), "dist_ft": round(track[after.ts], 1)},
            "fraction": round(frac, 4),
            "estimate_ts": round(before.ts + frac * (after.ts - before.ts), 1),
        }

    # the GPS dot worked in detail: the first report past the stop
    dot = next(f for f in near_fixes if track[f.ts] > stop_d + 200)
    kx = FEET_PER_DEGREE_LAT * math.cos(math.radians(pts[0][0]))
    px, py = (dot.lon - pts[0][1]) * kx, (dot.lat - pts[0][0]) * FEET_PER_DEGREE_LAT
    best = None
    for i in range(len(pts) - 1):
        off, dist = shape._segment(i, px, py)  # noqa: SLF001 - the same function the pipeline uses
        if best is None or off < best[0]:
            best = (off, dist, i)
    off, dist, i = best  # type: ignore[misc]
    a, b = pts[i], pts[i + 1]
    seg_len = math.hypot(shape.x[i + 1] - shape.x[i], shape.y[i + 1] - shape.y[i])
    frac = (dist - a[2]) / (b[2] - a[2])
    prev = [f for f in near_fixes if f.ts < dot.ts][-1]
    win_lo = track[prev.ts] - BACK_TOL_FT
    win_hi = track[prev.ts] + MAX_SPEED_FT_S * (dot.ts - prev.ts) + FORWARD_SLACK_FT
    window_pieces = sum(
        1 for p, nxt in zip(pts, pts[1:], strict=False) if nxt[2] > win_lo and p[2] < win_hi
    )

    example = {
        "date": day,
        "route": route_name,
        "headsign": headsign,
        "n_route_points": len(pts),
        "n_stops": len(stops),
        "n_reports": len(fixes),
        "first_report_ts": int(fixes[0].ts),
        "last_report_ts": int(fixes[-1].ts),
        "stop": {
            "sequence": int(stop[0]),
            "name": stop[6],
            "dist_ft": stop_d,
            "scheduled_arrival_ts": int(stop[3]),
            "scheduled_departure_ts": int(stop[4]),
        },
        "arrive_ft_before": ARRIVE_TOL_FT,
        "depart_ft_after": DEPART_TOL_FT,
        "stops_near": [
            {"sequence": int(s[0]), "name": s[6], "dist_ft": float(s[2]), "timepoint": s[5] == "1"}
            for s in stops
            if abs(float(s[2]) - stop_d) <= 1800
        ],
        "route_points_near": [
            {"dist_ft": round(p[2], 1), "lat": p[0], "lon": p[1]}
            for p in pts
            if abs(p[2] - stop_d) <= 1100
        ],
        "reports": [{"ts": int(f.ts), "dist_ft": round(track[f.ts], 1)} for f in near_fixes],
        "arrival": crossing(stop_d - ARRIVE_TOL_FT),
        "departure": crossing(stop_d + DEPART_TOL_FT),
        "stored": {
            "arr_lo": visit.arr_lo,
            "arr_hi": visit.arr_hi,
            "arr_est": visit.arr_est,
            "dep_lo": visit.dep_lo,
            "dep_hi": visit.dep_hi,
            "dep_est": visit.dep_est,
        },
        "projection": {
            "report_ts": int(dot.ts),
            "feet_per_degree_latitude": FEET_PER_DEGREE_LAT,
            "longitude_shrink": round(math.cos(math.radians(pts[0][0])), 3),
            "east_ft": round(px),
            "north_ft": round(py),
            "n_pieces": len(pts) - 1,
            "piece_index": i,
            "a": {"lat": a[0], "lon": a[1], "dist_ft": round(a[2], 1)},
            "b": {"lat": b[0], "lon": b[1], "dist_ft": round(b[2], 1)},
            "piece_length_ft": round(seg_len),
            "fraction": round(frac, 3),
            "offset_ft": round(off),
            "dist_ft": round(dist),
            "window": {
                "previous_ts": int(prev.ts),
                "previous_dist_ft": round(track[prev.ts], 1),
                "elapsed_s": int(dot.ts - prev.ts),
                "back_ft": BACK_TOL_FT,
                "max_speed_ft_s": MAX_SPEED_FT_S,
                "slack_ft": FORWARD_SLACK_FT,
                "lo_ft": round(win_lo),
                "hi_ft": round(win_hi),
                "pieces": window_pieces,
            },
        },
    }
    Path(args.out).write_text(json.dumps(example, separators=(",", ":")))
    print(f"wrote {args.out}: {len(json.dumps(example)):,} bytes; stop #{stop[0]} {stop[6]}")
    print(
        "arrival estimate",
        example["arrival"]["estimate_ts"],
        "stored",
        visit.arr_est,
        "| departure",
        example["departure"]["estimate_ts"],
        "stored",
        visit.dep_est,
    )


if __name__ == "__main__":
    main()
