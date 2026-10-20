# ruff: noqa: E501  (SQL is kept on readable lines)
"""Write a proof report you can check with your own eyes.

    uv run python scripts/proof_report.py --profile enpu            # prints, and saves under docs/data_reports/
    uv run python scripts/proof_report.py --profile enpu --seed other --day 2026-10-16

Part 1 lists the newest result of every monitoring check, with its SQL. Part 2 picks five random
trips of a complete service date and shows, for a few stops of each, the schedule, our inferred
arrival, and the two raw GPS fixes on either side with their distance from the stop. To verify: look
up the stop on a map, and ask whether a bus at that distance at those wall-clock times (Pacific) is
consistent with the arrival we claim. Needs the SQL warehouse to be running.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_reliability.monitoring.proof import (  # noqa: E402
    evenly_spaced,
    feet_between,
    markdown_table,
    pacific,
)

PREFIX = "transit"


def api(profile: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    cmd = ["databricks", "api", method, path, "--profile", profile]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode or not done.stdout:
        raise RuntimeError(done.stderr.strip()[:300] or "no response (is the warehouse running?)")
    return json.loads(done.stdout)


def query(profile: str, warehouse: str, sql: str) -> list[dict[str, Any]]:
    """All rows as dicts, following every result page and checking the total."""

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
    names = [c["name"] for c in d["manifest"]["schema"]["columns"]]
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
    return [dict(zip(names, r, strict=True)) for r in rows]


def checks_section(run: Any) -> str:
    try:
        rows = run(
            f"SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY suite, check_name ORDER BY run_ts DESC) AS rn "
            f"FROM workspace.{PREFIX}_ops.check_results) WHERE rn = 1 ORDER BY status DESC, suite, check_name"
        )
    except RuntimeError as error:
        return f"No check results yet ({error})."
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    head = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    body = markdown_table(
        ["status", "suite", "check", "value", "rule", "last run (UTC)"],
        [
            [r["status"], r["suite"], r["check_name"], r["value"], r["rule"], r["run_ts"]]
            for r in rows
        ],
    )
    return f"Newest result of each check: {head}.\n\n{body}\n\nEvery check's SQL is in column `sql` of `workspace.{PREFIX}_ops.check_results`; paste it into the SQL editor to get the same number."


def trip_section(run: Any, day: str, trip: dict[str, Any]) -> str:
    tid = trip["trip_id"]
    visits = run(
        f"SELECT v.stop_sequence, s.stop_name, CAST(s.stop_lat AS DOUBLE) lat, CAST(s.stop_lon AS DOUBLE) lon, v.timepoint, "
        f"v.sched_arr, v.arr_lo, v.arr_est, v.arr_hi, v.arr_method, v.arr_delay_s "
        f"FROM workspace.{PREFIX}_silver.stop_visits v "
        f"JOIN workspace.{PREFIX}_silver.scheduled_trips t ON t.service_date = v.service_date AND t.trip_id = v.trip_id "
        f"JOIN workspace.{PREFIX}_silver.gtfs_stops s ON s.feed_version = t.feed_version AND s.stop_id = v.stop_id "
        f"WHERE v.service_date = DATE'{day}' AND v.trip_id = '{tid}' ORDER BY v.stop_sequence"
    )
    fixes = run(
        f"SELECT vehicle_ts, CAST(latitude AS DOUBLE) lat, CAST(longitude AS DOUBLE) lon FROM workspace.{PREFIX}_silver.vehicle_positions "
        f"WHERE service_date = DATE'{day}' AND trip_id = '{tid}' ORDER BY vehicle_ts"
    )
    measured = [i for i, v in enumerate(visits) if v["arr_est"]]
    shown = [visits[i] for i in evenly_spaced(measured, 6)]
    rows = []
    for v in shown:
        est = v["arr_est"]
        before = [f for f in fixes if f["vehicle_ts"] <= est]
        after = [f for f in fixes if f["vehicle_ts"] > est]
        slat, slon = float(v["lat"]), float(v["lon"])

        def describe(f: dict[str, Any] | None) -> str:
            if f is None:
                return "-"
            ft = feet_between(slat, slon, float(f["lat"]), float(f["lon"]))
            return f"{pacific(f['vehicle_ts'])} ({ft:,.0f} ft away)"

        rows.append(
            [
                v["stop_sequence"],
                v["stop_name"],
                f"{slat:.5f}, {slon:.5f}",
                pacific(v["sched_arr"]),
                f"{pacific(est)} (between {pacific(v['arr_lo'])} and {pacific(v['arr_hi'])})",
                v["arr_method"],
                describe(before[-1] if before else None),
                describe(after[0] if after else None),
            ]
        )
    table = markdown_table(
        [
            "seq",
            "stop",
            "stop location",
            "scheduled",
            "our arrival estimate",
            "method",
            "last fix before",
            "first fix after",
        ],
        rows,
    )
    return (
        f"### Route {trip['route_short_name']} to {trip['trip_headsign']} (trip {tid}, vehicle {trip['vehicle_id']})\n"
        f"{len(visits)} stops, {len(measured)} with a measured arrival, {len(fixes)} GPS fixes. Wall-clock times are Pacific.\n\n{table}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--day", help="service date; default is the newest complete day (two days ago, Pacific)"
    )
    parser.add_argument("--seed", default=dt.date.today().isoformat(), help="same seed, same trips")
    parser.add_argument("--out", help="file to write; default docs/data_reports/proof_<day>.md")
    args = parser.parse_args()
    warehouse = json.loads(
        subprocess.run(
            ["databricks", "warehouses", "list", "-o", "json", "--profile", args.profile],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]["id"]

    def run(sql: str) -> list[dict[str, Any]]:
        return query(args.profile, warehouse, sql)

    day = args.day or str(dt.date.today() - dt.timedelta(days=2))
    trips = run(
        f"SELECT t.trip_id, t.route_short_name, t.trip_headsign, "
        f"(SELECT max(vehicle_id) FROM workspace.{PREFIX}_silver.vehicle_positions p "
        f"WHERE p.service_date = t.service_date AND p.trip_id = t.trip_id) AS vehicle_id "
        f"FROM workspace.{PREFIX}_gold.trip_performance t "
        f"WHERE t.service_date = DATE'{day}' AND t.trip_status = 'observed' "
        f"AND t.n_stops_measured >= 0.8 * t.n_stops "
        f"ORDER BY hash(t.trip_id, '{args.seed}') LIMIT 5"
    )
    parts = [
        f"# Proof report: service date {day}, seed `{args.seed}`",
        f"Generated {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M UTC}.",
        "## 1. Monitoring checks\n" + checks_section(run),
        "## 2. Five random trips: compare with a map or a live arrival app\n"
        + "\n\n".join(trip_section(run, day, t) for t in trips),
    ]
    text = "\n\n".join(parts) + "\n"
    out = Path(args.out) if args.out else Path("docs/data_reports") / f"proof_{day}.md"
    out.write_text(text)
    print(text)
    print(f"saved to {out}")


if __name__ == "__main__":
    main()
