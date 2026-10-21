# ruff: noqa: E501, E702, E731  (a throwaway analysis script, dense on purpose)
"""Replay the map's position estimate on real trips and measure how often the drawn bus moves BACKWARD.

Reports arrive LAG seconds after their GPS time stamp (60 s here). Each second W the map draws a position:
  A = what the page does now: estimate = last report + 0.75 x recent speed x age, and after each new report the
      drawn bus eases from where it was to the new estimate over 4 s (so a lower new estimate pulls it backward)
  B = same estimate, but the drawn bus never moves backward: it waits until the estimate catches up, and
      catches up no faster than 25 m/s
  R = just the last report (no estimate)"""

import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
from make_site_example import make_query

from transit_reliability.arrivals.infer import Fix, Shape, _project_track

q = make_query("enpu")
S = "workspace.transit_silver"
DAY = "2026-10-20"
LAG = 60
trips = [
    t[0]
    for t in q(f"""SELECT trip_id FROM {S}.vehicle_positions WHERE service_date=DATE'{DAY}' AND trip_id IS NOT NULL
  GROUP BY trip_id HAVING count(*) >= 150 ORDER BY hash(trip_id) LIMIT 120""")
]
ids = ",".join(f"'{t}'" for t in trips)
shape_of = dict(
    q(
        f"""SELECT st.trip_id, t.shape_id FROM {S}.scheduled_trips st JOIN {S}.gtfs_trips t ON t.feed_version=st.feed_version AND t.trip_id=st.trip_id WHERE st.service_date=DATE'{DAY}' AND st.trip_id IN ({ids})"""
    )
)
sid = ",".join(f"'{s}'" for s in set(shape_of.values()))
pts = {}
for s, la, lo, d in q(
    f"""SELECT shape_id, shape_pt_lat, shape_pt_lon, shape_dist_traveled FROM {S}.gtfs_shapes WHERE shape_id IN ({sid}) AND feed_version=(SELECT max(feed_version) FROM {S}.gtfs_shapes) ORDER BY shape_id, shape_pt_sequence"""
):
    pts.setdefault(s, []).append((float(la), float(lo), float(d)))
fixes = {}
for t, ts, la, lo, sq, st in q(
    f"""SELECT trip_id, unix_timestamp(vehicle_ts), latitude, longitude, current_stop_sequence, current_status FROM {S}.vehicle_positions WHERE service_date=DATE'{DAY}' AND trip_id IN ({ids}) ORDER BY trip_id, vehicle_ts"""
):
    fixes.setdefault(t, []).append(
        Fix(float(ts), float(la), float(lo), None if sq is None else int(sq), st == "STOPPED_AT")
    )
FT = 0.3048


def speed(hist):
    if len(hist) < 2:
        return None
    nt, nd = hist[-1]
    for ts, d in reversed(hist[:-1]):
        if nt - ts >= 15:
            return None if nt - ts > 60 else max(0.0, min(24.4, (nd - d) / (nt - ts)))
    return None


tot = {k: dict(back_m=0.0, secs=0, back_secs=0, errs=[]) for k in "ARBC"}
n_trips = 0
events = []
hold = dict(secs=0, total=0, ahead=[], runs=[])
run_len = 0
stall = {"B": [0, 0], "C": [0, 0]}  # [stalled seconds, seconds with a bus moving > 3 m/s]
for trip, fx in fixes.items():
    sh = shape_of.get(trip)
    if sh not in pts or len(pts[sh]) < 2:
        continue
    tr = _project_track(Shape(pts[sh]), sorted(fx, key=lambda f: f.ts))
    ts = [a for a, _ in tr]
    ds = [b * FT for _, b in tr]
    if len(ts) < 60:
        continue
    n_trips += 1

    def truth(w):
        import bisect

        i = bisect.bisect_right(ts, w) - 1
        if i < 0 or i + 1 >= len(ts) or ts[i + 1] - ts[i] > 60:
            return None
        return ds[i] + (ds[i + 1] - ds[i]) * (w - ts[i]) / (ts[i + 1] - ts[i])

    hist = []
    known = -1
    a_off = 0.0
    a_t0 = None
    b_shown = None
    c_shown = None
    prev = {"A": None, "B": None, "C": None, "R": None}
    for w in range(int(ts[0]) + LAG + 30, int(ts[-1]) + LAG - 30):
        while known + 1 < len(ts) and ts[known + 1] <= w - LAG:
            known += 1
            hist.append((ts[known], ds[known]))
            if len(hist) > 5:
                hist.pop(0)
            new_report = True
            if known > 0 and prev["A"] is not None:
                v = speed(hist)
                if v is not None:
                    e_new = min(ds[-1] + 1e9, ds[known] + 0.75 * v * min(120, w - ts[known]))
                    a_off = prev["A"] - e_new
                    a_t0 = w
        if known < 1:
            continue
        v = speed(hist)
        tt = truth(w)
        if v is None or tt is None:
            continue
        est = ds[known] + 0.75 * v * min(120, w - ts[known])
        a = est + (a_off * max(0.0, 1 - (w - a_t0) / 4) if a_t0 is not None else 0.0)
        was = b_shown
        was_c = c_shown
        if c_shown is None:
            c_shown = est
        elif est > c_shown:
            c_shown += min(est - c_shown, 25.0)
        else:  # slow down in proportion to how far ahead of the estimate the bus is drawn, but keep going forward
            c_shown += max(0.0, 0.75 * v * (1 - (c_shown - est) / 400.0))
        b_shown = (
            est
            if b_shown is None
            else (b_shown if est <= b_shown else b_shown + min(est - b_shown, 25.0))
        )
        r = ds[known]
        held = was is not None and est < was
        if held:
            hold["secs"] += 1
            hold["ahead"].append(was - est)
            run_len += 1
        else:
            if run_len:
                hold["runs"].append(run_len)
            run_len = 0
        hold["total"] += 1
        if v > 3 and was is not None and was_c is not None:
            stall["B"][1] += 1
            stall["C"][1] += 1
            stall["B"][0] += (b_shown - was) < 0.5
            stall["C"][0] += (c_shown - was_c) < 0.5
        for k, val in (("A", a), ("B", b_shown), ("C", c_shown), ("R", r)):
            tot[k]["secs"] += 1
            tot[k]["errs"].append(abs(val - tt))
            if prev[k] is not None and val < prev[k] - 0.5:
                tot[k]["back_m"] += prev[k] - val
                tot[k]["back_secs"] += 1
            prev[k] = val


def pct(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))]


print("trips replayed:", n_trips, "| report delay assumed:", LAG, "s\n")
print(
    f"{'':32}{'moves backward':>16}{'avg backward':>16}{'typical error':>16}{'9 in 10 within':>16}"
)
for k, name in (
    ("R", "last report only"),
    ("A", "estimate now (eases)"),
    ("B", "estimate, never backward, waits"),
    ("C", "estimate, never backward, slows"),
):
    t = tot[k]
    print(
        f"{name:32}{t['back_secs'] / t['secs']:>15.1%}{t['back_m'] / (t['secs'] / 3600):>13.0f} m/h{pct(t['errs'], 0.5):>13.0f} m{pct(t['errs'], 0.9):>13.0f} m"
    )

print()
print("When the estimate falls BEHIND the drawn bus (version B), the bus waits where it is:")
print(f"  share of the time the bus is waiting: {hold['secs'] / hold['total']:.1%}")
if hold["runs"]:
    print(
        f"  length of one wait, seconds (median / 90th percentile / longest): {pct(hold['runs'], 0.5):.0f} / {pct(hold['runs'], 0.9):.0f} / {max(hold['runs'])}"
    )
    print(
        f"  how far ahead of the estimate the drawn bus sits while waiting, metres (median / 90th percentile): {pct(hold['ahead'], 0.5):.0f} / {pct(hold['ahead'], 0.9):.0f}"
    )
print()
print("While a bus is really moving (> 3 m/s), how often is the drawn bus standing still?")
print(
    f"  waits (B): {stall['B'][0] / stall['B'][1]:.1%}   slows instead (C): {stall['C'][0] / stall['C'][1]:.1%}"
)
