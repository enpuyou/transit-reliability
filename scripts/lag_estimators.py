# ruff: noqa: E501  (SQL is kept on readable lines)
"""Which way of drawing a bus "now" is closest to where it really is, given that reports arrive late?

    uv run python scripts/lag_estimators.py --profile enpu --days 2026-10-16 2026-10-20 --trips 120

For a sample of trips per day, replay the day second by second as the map would see it:
- a report becomes visible when the agency first published it (its first appearance in our bronze table) plus
  OUR_DELAY_S for the relay and the page (relay memory up to 15 s, polling every 10 s);
- the agency's predictions become visible when they first appeared in the feed, plus OUR_DELAY_S.

At each moment W (every STEP_S seconds) four estimates of the bus's distance along its route are compared with the
TRUTH: where the bus really was at W, interpolated between its own reports by the GPS time each carries (known
afterwards, so it is not affected by the delay).

- last report: the newest visible report, unchanged (what the feed says);
- recent speed: last report + 75% of the recent speed x age (what the map does now);
- timetable pace: keep the bus as late as it was at its last report and move it at the timetable's pace
  (slow stretches and timepoints are in the timetable), never behind the last report;
- agency prediction: where the agency's newest visible predicted arrival times put the bus at W (interpolated
  between consecutive stops), never behind the last report; recent speed if there is no prediction.

Distances come from projecting each GPS report onto the trip's route line (the pipeline's own function), so all
errors are measured along the route, in metres.
"""

import argparse
import bisect
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from make_site_example import make_query  # noqa: E402

from transit_reliability.arrivals.infer import Fix, Shape, _project_track  # noqa: E402

S, B = "workspace.transit_silver", "workspace.transit_bronze"
FT = 0.3048
OUR_DELAY_S = 12
STEP_S = 5
FACTOR, MAX_AGE_S = 0.75, 120


def speed(hist: list[tuple[float, float]]) -> float | None:
    """Recent speed (m/s) from visible reports, newest last; the same rule as site/estimate.js."""

    if len(hist) < 2:
        return None
    nt, nd = hist[-1]
    for ts, d in reversed(hist[:-1]):
        if nt - ts >= 15:
            return None if nt - ts > 60 else max(0.0, min(24.4, (nd - d) / (nt - ts)))
    return None


def interp(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear y(x) for increasing xs, clamped at the ends."""

    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x) - 1
    return (
        ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])
        if xs[i + 1] > xs[i]
        else ys[i]
    )


def load_day(q, day: str, n_trips: int) -> dict:
    trips = [
        t[0]
        for t in q(f"""SELECT trip_id FROM {S}.vehicle_positions WHERE service_date = DATE'{day}' AND trip_id IS NOT NULL
        GROUP BY trip_id HAVING count(*) >= 120 ORDER BY hash(trip_id, '{day}') LIMIT {n_trips}""")
    ]
    ids = ",".join(f"'{t}'" for t in trips)
    shape_of = dict(
        q(f"""SELECT st.trip_id, t.shape_id FROM {S}.scheduled_trips st JOIN {S}.gtfs_trips t
        ON t.feed_version = st.feed_version AND t.trip_id = st.trip_id WHERE st.service_date = DATE'{day}' AND st.trip_id IN ({ids})""")
    )
    sids = ",".join(f"'{s}'" for s in set(shape_of.values()))
    pts: dict[str, list[tuple[float, float, float]]] = {}
    for (
        sid,
        lat,
        lon,
        dist,
    ) in q(f"""SELECT shape_id, shape_pt_lat, shape_pt_lon, shape_dist_traveled FROM {S}.gtfs_shapes
        WHERE shape_id IN ({sids}) AND feed_version = (SELECT max(feed_version) FROM {S}.gtfs_shapes) ORDER BY shape_id, shape_pt_sequence"""):
        pts.setdefault(sid, []).append((float(lat), float(lon), float(dist)))
    stops: dict[str, list[tuple[int, float, float]]] = {}
    for (
        trip,
        seq,
        dist,
        arr,
    ) in q(f"""SELECT sst.trip_id, sst.stop_sequence, st.shape_dist_traveled, unix_timestamp(sst.scheduled_arrival_ts)
        FROM {S}.scheduled_stop_times sst JOIN {S}.gtfs_stop_times st ON st.feed_version = sst.feed_version AND st.trip_id = sst.trip_id AND st.stop_sequence = sst.stop_sequence
        WHERE sst.service_date = DATE'{day}' AND sst.trip_id IN ({ids}) ORDER BY sst.trip_id, sst.stop_sequence"""):
        if dist is not None:
            stops.setdefault(trip, []).append((int(seq), float(dist), float(arr)))
    fixes: dict[str, list[tuple[Fix, float]]] = {}
    for (
        trip,
        ts,
        lat,
        lon,
        published,
    ) in q(f"""SELECT v.trip_id, unix_timestamp(v.vehicle_ts), v.latitude, v.longitude, b.published
        FROM {S}.vehicle_positions v JOIN (
          SELECT trip_id, vehicle_ts, min(feed_header_ts) AS published FROM {B}.vehicle_positions
          WHERE trip_id IN ({ids}) AND to_date(timestamp_seconds(feed_header_ts)) BETWEEN DATE'{day}' - INTERVAL 1 DAY AND DATE'{day}' + INTERVAL 1 DAY
          GROUP BY trip_id, vehicle_ts) b ON b.trip_id = v.trip_id AND b.vehicle_ts = unix_timestamp(v.vehicle_ts)
        WHERE v.service_date = DATE'{day}' AND v.trip_id IN ({ids}) ORDER BY v.trip_id, v.vehicle_ts"""):
        fixes.setdefault(trip, []).append(
            (Fix(float(ts), float(lat), float(lon), None, False), float(published))
        )
    preds: dict[str, dict[int, list[tuple[float, float]]]] = {}
    for (
        trip,
        seq,
        at,
        first_seen,
    ) in q(f"""SELECT trip_id, stop_sequence, unix_timestamp(coalesce(arrival_time, departure_time)), unix_timestamp(first_seen)
        FROM {S}.trip_update_snapshots WHERE service_date = DATE'{day}' AND trip_id IN ({ids}) AND coalesce(arrival_time, departure_time) IS NOT NULL
        ORDER BY trip_id, stop_sequence, first_seen"""):
        preds.setdefault(trip, {}).setdefault(int(seq), []).append((float(first_seen), float(at)))
    return {
        "trips": trips,
        "shape_of": shape_of,
        "pts": pts,
        "stops": stops,
        "fixes": fixes,
        "preds": preds,
    }


def replay(data: dict, out: dict) -> None:
    for trip in data["trips"]:
        sid, fx, st = data["shape_of"].get(trip), data["fixes"].get(trip), data["stops"].get(trip)
        if sid not in data["pts"] or not fx or not st or len(st) < 3:
            continue
        order = sorted(fx, key=lambda p: p[0].ts)
        track = _project_track(Shape(data["pts"][sid]), [f for f, _ in order])
        pub = {f.ts: p for f, p in order}
        ts = [a for a, _ in track]
        ds = [b * FT for _, b in track]
        if len(ts) < 40:
            continue
        seen = [pub[t] + OUR_DELAY_S for t in ts]  # when the map could first show each report
        stop_d = [d * FT for _, d, _ in st]
        stop_t = [t for _, _, t in st]
        seq_of = [s for s, _, _ in st]
        # timetable pace: the scheduled time at a distance (monotone in distance along the trip)
        sched_d, sched_t = [], []
        for d, t in zip(stop_d, stop_t, strict=True):
            if not sched_d or (d > sched_d[-1] and t >= sched_t[-1]):
                sched_d.append(d)
                sched_t.append(t)
        if len(sched_d) < 2:
            continue
        pr = data["preds"].get(trip, {})
        out["trips"] += 1
        w = min(seen) + 30
        end = max(ts) - 30
        while w < end:
            vis = [i for i in range(len(ts)) if seen[i] <= w]
            if not vis:
                w += STEP_S
                continue
            k = max(vis, key=lambda i: ts[i])
            j = bisect.bisect_right(ts, w) - 1
            if j < 0 or j + 1 >= len(ts) or ts[j + 1] - ts[j] > 60 or ts[k] > w:
                w += STEP_S
                continue
            truth = ds[j] + (ds[j + 1] - ds[j]) * (w - ts[j]) / (ts[j + 1] - ts[j])
            hist = sorted(((ts[i], ds[i]) for i in vis), key=lambda x: x[0])[-5:]
            age = min(MAX_AGE_S, w - ts[k])
            v = speed(hist)
            last = ds[k]
            est = {"last report": last}
            est["recent speed"] = last + (FACTOR * v * age if v is not None else 0.0)
            # timetable pace
            delay = ts[k] - interp(sched_d, sched_t, last)
            est["timetable pace"] = max(last, interp(sched_t, sched_d, w - delay))
            # agency prediction: newest visible prediction for each upcoming stop
            pd_, pt_ = [last], [ts[k]]
            for s, d in zip(seq_of, stop_d, strict=True):
                if d <= last + 1 or s not in pr:
                    continue
                vis_p = [at for fs, at in pr[s] if fs + OUR_DELAY_S <= w]
                if vis_p and vis_p[-1] > pt_[-1]:
                    pd_.append(d)
                    pt_.append(vis_p[-1])
            est["agency prediction"] = (
                max(last, interp(pt_, pd_, w)) if len(pd_) > 1 else est["recent speed"]
            )
            moving = v is not None and v > 3
            # mixed: timetable pace while the bus was moving at its last report, recent speed when slow or stopped
            est["mixed"] = est["timetable pace"] if moving else est["recent speed"]
            # mixed, halfway: while moving, halfway between timetable pace and recent speed
            est["mixed, halfway"] = (
                (est["timetable pace"] + est["recent speed"]) / 2 if moving else est["recent speed"]
            )
            for name, e in est.items():
                err = abs(e - truth)
                out["err"].setdefault(name, []).append(err)
                out["err_moving" if moving else "err_slow"].setdefault(name, []).append(err)
                out["worse"].setdefault(name, []).append(err - abs(last - truth))
                out["signed"].setdefault(name, []).append(
                    e - truth
                )  # + ahead of the bus, - behind it
            out["age"].append(w - ts[k])
            w += STEP_S


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[int(p * (len(s) - 1))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--days", nargs="+", default=["2026-10-16", "2026-10-20"])
    ap.add_argument("--trips", type=int, default=120)
    ap.add_argument(
        "--save", default="/tmp/lag_estimators.pkl", help="where to keep the raw results"
    )
    args = ap.parse_args()
    q = make_query(args.profile)
    out: dict = {
        "trips": 0,
        "err": {},
        "err_moving": {},
        "err_slow": {},
        "worse": {},
        "signed": {},
        "age": [],
    }
    for day in args.days:
        replay(load_day(q, day, args.trips), out)
    # keep the raw results so the report can be reprinted without replaying (minutes of queries)
    import pickle

    with open(args.save, "wb") as f:
        pickle.dump(out, f)
    report(out, args.days)


def report(out: dict, days: list[str]) -> None:
    print(
        f"days {' '.join(days)} | trips replayed {out['trips']} | moments compared {len(out['age'])} (every {STEP_S} s)"
    )
    print(
        f"age of the newest visible report at those moments: median {statistics.median(out['age']):.0f} s, 90th percentile {pct(out['age'], 0.9):.0f} s\n"
    )
    print(
        f"{'error along the route, metres':34}{'typical':>9}{'9 in 10':>9}{'99 in 100':>11}{'worse than last report':>26}{'>200 m worse':>14}"
    )
    for name, errs in out["err"].items():
        worse = out["worse"][name]
        print(
            f"{name:34}{pct(errs, 0.5):>9.0f}{pct(errs, 0.9):>9.0f}{pct(errs, 0.99):>11.0f}{sum(1 for x in worse if x > 25) / len(worse):>25.0%}{sum(1 for x in worse if x > 200) / len(worse):>14.1%}"
        )
    print(
        "\nahead of or behind the real bus, metres along the route (+ drawn ahead of the bus, - behind it):"
    )
    print(f"{'':34}{'median':>9}{'ahead':>8}{'behind':>8}{'ahead >200 m':>14}{'behind >200 m':>15}")
    for name, xs in out["signed"].items():
        n = len(xs)
        print(
            f"{name:34}{statistics.median(xs):>9.0f}{sum(1 for x in xs if x > 25) / n:>8.0%}{sum(1 for x in xs if x < -25) / n:>8.0%}"
            f"{sum(1 for x in xs if x > 200) / n:>14.1%}{sum(1 for x in xs if x < -200) / n:>15.1%}"
        )
    for label, key in (
        ("bus moving at its last report (> 3 m/s)", "err_moving"),
        ("bus slow or stopped at its last report", "err_slow"),
    ):
        print(f"\n{label}:")
        for name, errs in out[key].items():
            print(f"  {name:32}{pct(errs, 0.5):>9.0f}{pct(errs, 0.9):>9.0f}  (n={len(errs)})")


if __name__ == "__main__":
    main()
