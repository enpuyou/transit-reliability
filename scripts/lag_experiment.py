"""Does moving a bus forward along its route by its recent speed beat leaving it at its last report?

For a sample of trips on one day: project each GPS report onto the route line (the pipeline's own function),
then for each report i and horizon H, compare the true position about H seconds later with (a) the report
itself and (b) the report plus recent speed x elapsed time (possibly damped)."""

import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
from make_site_example import make_query  # noqa: E402

from transit_reliability.arrivals.infer import Fix, Shape, _project_track  # noqa: E402

q = make_query("enpu")
S = "workspace.transit_silver"
DAY = "2026-10-20"
trips = q(f"""SELECT trip_id FROM {S}.vehicle_positions WHERE service_date = DATE'{DAY}' AND trip_id IS NOT NULL
  GROUP BY trip_id HAVING count(*) >= 150 ORDER BY hash(trip_id) LIMIT 250""")
ids = ",".join(f"'{t[0]}'" for t in trips)
print("trips sampled:", len(trips))
shape_of = dict(
    q(f"""SELECT st.trip_id, t.shape_id FROM {S}.scheduled_trips st JOIN {S}.gtfs_trips t
  ON t.feed_version = st.feed_version AND t.trip_id = st.trip_id WHERE st.service_date = DATE'{DAY}' AND st.trip_id IN ({ids})""")
)
shape_ids = ",".join(f"'{s}'" for s in set(shape_of.values()))
pts: dict[str, list[tuple[float, float, float]]] = {}
for (
    sid,
    lat,
    lon,
    dist,
) in q(f"""SELECT shape_id, shape_pt_lat, shape_pt_lon, shape_dist_traveled FROM {S}.gtfs_shapes
  WHERE shape_id IN ({shape_ids}) AND feed_version = (SELECT max(feed_version) FROM {S}.gtfs_shapes) ORDER BY shape_id, shape_pt_sequence"""):
    pts.setdefault(sid, []).append((float(lat), float(lon), float(dist)))
rows = q(f"""SELECT trip_id, unix_timestamp(vehicle_ts), latitude, longitude, current_stop_sequence, current_status
  FROM {S}.vehicle_positions WHERE service_date = DATE'{DAY}' AND trip_id IN ({ids}) ORDER BY trip_id, vehicle_ts""")
fixes: dict[str, list[Fix]] = {}
for trip, ts, lat, lon, seq, status in rows:
    fixes.setdefault(trip, []).append(
        Fix(
            float(ts),
            float(lat),
            float(lon),
            None if seq is None else int(seq),
            status == "STOPPED_AT",
        )
    )
print("fixes:", len(rows))

HORIZONS = [30, 45, 60, 75]
FACTORS = [0.0, 0.5, 0.75, 1.0]  # 0 = leave at last report
errs = {(h, k): [] for h in HORIZONS for k in FACTORS}
moving = {(h, k): [] for h in HORIZONS for k in FACTORS}
n_trips = 0
for trip, fx in fixes.items():
    sid = shape_of.get(trip)
    if sid not in pts or len(pts[sid]) < 2:
        continue
    track = _project_track(Shape(pts[sid]), sorted(fx, key=lambda f: f.ts))
    t = [a for a, _ in track]
    d = [b for _, b in track]
    if len(t) < 40:
        continue
    n_trips += 1
    for i in range(3, len(t)):
        # recent speed from the oldest report 15 to 45 s back
        m = next((j for j in range(i - 1, -1, -1) if t[i] - t[j] >= 15), None)
        if m is None or t[i] - t[m] > 60:
            continue
        v = max(0.0, min(80.0, (d[i] - d[m]) / (t[i] - t[m])))
        for h in HORIZONS:
            j = next((j for j in range(i + 1, len(t)) if t[j] - t[i] >= h), None)
            if j is None or t[j] - t[i] > h + 10:
                continue
            gap = t[j] - t[i]
            for k in FACTORS:
                e = abs(d[j] - (d[i] + k * v * gap))
                errs[(h, k)].append(e)
                if v > 3:
                    moving[(h, k)].append(e)
print("trips used:", n_trips)


def pct(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))]


print(
    "\nerror in FEET between the drawn position and where the bus really was H seconds after the report"
)
print(
    "(all reports)           H   leave-as-is   +50% speed   +75% speed   +100% speed   [median / 90th percentile]"
)
for h in HORIZONS:
    print(
        f"{'':24}{h:>3}s  "
        + "   ".join(
            f"{pct(errs[(h, k)], 0.5):>5.0f}/{pct(errs[(h, k)], 0.9):<5.0f}" for k in FACTORS
        ),
        f" n={len(errs[(h, 0.0)])}",
    )
print("(bus moving > 3 ft/s)")
for h in HORIZONS:
    print(
        f"{'':24}{h:>3}s  "
        + "   ".join(
            f"{pct(moving[(h, k)], 0.5):>5.0f}/{pct(moving[(h, k)], 0.9):<5.0f}" for k in FACTORS
        ),
        f" n={len(moving[(h, 0.0)])}",
    )
