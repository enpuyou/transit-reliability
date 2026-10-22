# ruff: noqa: E501  (long status strings)
"""Does the map's "Next departures from the start" panel tell the truth? Check it against what happens.

    uv run python scripts/check_departures.py snapshot --out /tmp/departures.json     # now: label upcoming trips
    uv run python scripts/check_departures.py recheck --file /tmp/departures.json     # 30+ min later: did they run?

`snapshot` labels every trip in the city that is due to start in the next 30 minutes and has not started, with the
same rules as site/map.js (updateDepartures): canceled, skips its first stops, first trip of the bus's day, bus
still on its previous trip, a bus should be waiting (previous trip ended within 250 m of its last stop), previous
trip ended elsewhere, previous trip never showed up, or not known. It reads the live files through the relay, the
relay's trip memory, and site/map/trips/*.json. `recheck` asks whether each trip then ran (it was in the bus file,
or the trip memory saw it finish) and prints, per label, how many ran.
"""

import argparse
import json
import math
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

from transit_reliability.gtfs_rt.parse import parse_vehicle_positions

RELAY = "https://transit-relay.youenpu.workers.dev"
TRIPS_DIR = Path("site/map/trips")
PT = ZoneInfo("America/Los_Angeles")
WAIT_RADIUS_M = 250


def get(url: str) -> bytes:
    # Cloudflare turns away Python's default user agent (403), so say who we are
    req = urllib.request.Request(
        url, headers={"User-Agent": "transit-reliability check_departures"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def service_base(yyyymmdd: str) -> float:
    """GTFS times count from noon minus 12 hours of the service day, in Seattle."""

    d = datetime.strptime(yyyymmdd, "%Y%m%d").replace(hour=12, tzinfo=PT)
    return (d - timedelta(hours=12)).timestamp()


def metres(a: list[float], b: list[float]) -> float:
    kx = 111320 * math.cos(math.radians(a[1]))
    return math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110540)


def memory(ids: list[str]) -> dict:
    out: dict = {"ended": {}, "running": [], "since": None}
    for i in range(0, len(ids), 60):
        part = json.loads(get(f"{RELAY}/memory/ended?trips={','.join(ids[i : i + 60])}"))
        out["ended"].update(part["ended"])
        out["running"] += part["running"]
        out["since"] = part["since"]
    return out


def snapshot(out_path: str) -> None:
    now = time.time()
    routes = {p.stem: json.loads(p.read_text()) for p in TRIPS_DIR.glob("*.json")}
    running = {
        r["trip_id"] for r in parse_vehicle_positions(get(f"{RELAY}/vehicles")) if r["trip_id"]
    }
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(get(f"{RELAY}/trips"))
    upcoming = []
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        t = e.trip_update
        data = routes.get(t.trip.route_id)
        info = data and data["trips"].get(t.trip.trip_id)
        if not info or t.trip.trip_id in running:
            continue
        first = info[5].split(";")[0].split(":")
        start = service_base(t.trip.start_date or datetime.now(PT).strftime("%Y%m%d")) + int(
            first[1]
        )
        if not now <= start <= now + 1800:
            continue
        skipped_first = 0
        for u in t.stop_time_update:
            if u.schedule_relationship != 1:
                break
            skipped_first += 1
        upcoming.append(
            {
                "trip": t.trip.trip_id,
                "route": t.trip.route_id,
                "start": start,
                "info": info,
                "canceled": t.trip.schedule_relationship == 3,
                "skipped_first": skipped_first,
                "date": t.trip.start_date,
                "stops": data["stops"],
            }
        )
    mem = memory(sorted({u["info"][1] for u in upcoming if u["info"][1]}))
    labelled = []
    for u in upcoming:
        prev, prev_last, prev_end_s = u["info"][1], u["info"][3], u["info"][4]
        prev_end = (
            None
            if prev_end_s is None
            else service_base(u["date"] or datetime.now(PT).strftime("%Y%m%d")) + prev_end_s
        )
        if u["canceled"]:
            label = "canceled"
        elif u["skipped_first"]:
            label = "skips first stops"
        elif not prev:
            label = "first trip of the bus's day"
        elif prev in running or prev in mem["running"]:
            label = "bus still on previous trip"
        elif prev in mem["ended"]:
            e = mem["ended"][prev]
            end = u["stops"].get(prev_last)
            near = (
                end and e["lon"] is not None and metres([e["lon"], e["lat"]], end) <= WAIT_RADIUS_M
            )
            label = "bus waiting" if near else "previous trip ended elsewhere"
        elif mem["since"] and prev_end and prev_end < now - 300 and mem["since"] < prev_end - 900:
            label = "previous trip never showed up"
        elif prev_end and prev_end > now:
            label = "previous trip not due to finish yet"
        else:
            label = "not known"
        labelled.append(
            {"trip": u["trip"], "route": u["route"], "start": u["start"], "label": label}
        )
    Path(out_path).write_text(json.dumps({"taken": now, "trips": labelled}))
    counts: dict[str, int] = {}
    for x in labelled:
        counts[x["label"]] = counts.get(x["label"], 0) + 1
    print(
        f"snapshot at {datetime.fromtimestamp(now, PT):%H:%M} Seattle: {len(labelled)} trips due to start in the next 30 min"
    )
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:36}{v:>5}")


def recheck(path: str) -> None:
    snap = json.loads(Path(path).read_text())
    ids = [t["trip"] for t in snap["trips"]]
    running = {
        r["trip_id"] for r in parse_vehicle_positions(get(f"{RELAY}/vehicles")) if r["trip_id"]
    }
    mem = memory(ids)
    ran = {i for i in ids if i in running or i in mem["running"] or i in mem["ended"]}
    print(
        f"recheck at {datetime.now(PT):%H:%M} Seattle, {(time.time() - snap['taken']) / 60:.0f} min after the snapshot"
    )
    print(f"  {'label at the snapshot':36}{'trips':>6}{'ran':>6}{'did not':>9}")
    by: dict[str, list[int]] = {}
    for t in snap["trips"]:
        by.setdefault(t["label"], [0, 0])[0 if t["trip"] in ran else 1] += 1
    for label, (yes, no) in sorted(by.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {label:36}{yes + no:>6}{yes:>6}{no:>9}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("snapshot")
    a.add_argument("--out", required=True)
    b = sub.add_parser("recheck")
    b.add_argument("--file", required=True)
    args = ap.parse_args()
    snapshot(args.out) if args.cmd == "snapshot" else recheck(args.file)


if __name__ == "__main__":
    main()
