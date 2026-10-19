"""Infer when a bus reached and left each stop of a trip from its GPS fixes. Pure; feet, seconds.

Method (design doc Phase 5, decision 0011):
1. Project each fix onto the trip's route shape to get *distance along the route*. After the first
   fix the search is limited to a window around the previous position, so an out-and-back route
   cannot flip a bus onto the wrong leg. Fixes far from the route are ignored. Motion is forced
   to be forward-only (GPS can step back a little).
2. A stop is *reached* when the distance first passes `stop distance - ARRIVE_TOL_FT` and *left*
   when it first passes `stop distance + DEPART_TOL_FT`. Each time is interpolated between the two
   bracketing fixes and reported as an interval [lo, hi] plus a point estimate, never as a
   false-precise instant. Brackets wider than MAX_GAP_S are labelled `wide_gap`.
3. Evidence from the feed's own stop tracking. In this feed `current_stop_sequence` is the NEXT stop
   to be served, also when the status is STOPPED_AT (measured: 99.3 percent of STOPPED_AT fixes sit
   at the stop before the one named). So the first fix whose next stop is past stop k
   (`advance_ts`) shows the bus has served k; and STOPPED_AT fixes near stop k that name the
   following stop are dwell evidence at k (`dwell_first`, `dwell_last`). Dwell caps the arrival and
   floors the departure. The pointer alone supplies an interval when the geometry cannot.

Distances are in the units of `shape_dist_traveled`, which for KCM is feet (checked against the
geometric length of 60 shapes: ratio 3.28).
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace

FEET_PER_DEGREE_LAT = 364_610.0  # 111,132.92 m per degree of latitude, in feet
ARRIVE_TOL_FT = 40.0  # a bus within this distance short of a stop point counts as arrived
DEPART_TOL_FT = 60.0  # a bus this far past the stop point has left it
OFFSET_LIMIT_FT = 250.0  # a fix farther than this from the route is ignored
BACK_TOL_FT = 400.0  # how far backward a projection may move (GPS jitter)
FORWARD_SLACK_FT = 600.0  # extra forward room on top of speed * elapsed time
MAX_SPEED_FT_S = 100.0  # about 68 mph; faster implied motion is rejected
MAX_GAP_S = 180.0  # a bracket wider than this is labelled wide_gap
TIE_FT = 60.0  # candidates this close to the best are treated as equally near (lane offsets)
SMALL_BACK_FT = 100.0  # a candidate more than this behind the previous position is implausible
DWELL_BEHIND_FT = 200.0  # a stopped fix counts as dwell at a stop if within this short of it...
DWELL_AHEAD_FT = 150.0  # ...or this far past it


@dataclass(frozen=True)
class Fix:
    ts: float
    lat: float
    lon: float
    next_seq: int | None = None  # the feed's current_stop_sequence: the NEXT stop to be served
    stopped: bool = False  # the feed's status is STOPPED_AT


@dataclass(frozen=True)
class StopTarget:
    stop_sequence: int
    stop_id: str
    dist: float  # distance along the shape, same units as the shape


@dataclass(frozen=True)
class StopVisit:
    stop_sequence: int
    stop_id: str
    arr_lo: float | None  # the arrival was not before this time
    arr_hi: float | None  # the arrival was not after this time
    arr_est: float | None  # point estimate inside [arr_lo, arr_hi]
    arr_method: str  # interpolated, wide_gap, next_stop_advance, before_first_fix, not_reached, ...
    dep_lo: float | None
    dep_hi: float | None
    dep_est: float | None
    dep_method: str
    advance_ts: float | None  # first fix whose next stop is past this one
    dwell_first: float | None  # first STOPPED_AT fix near this stop (next stop = the following one)
    dwell_last: float | None  # last such fix
    geo_arr_est: float | None = None  # the geometric arrival estimate before evidence adjustments


class Shape:
    """A route polyline with the provided distance along it at every vertex."""

    def __init__(self, points: Sequence[tuple[float, float, float]]) -> None:
        if len(points) < 2:
            raise ValueError("a shape needs at least two points")
        lat0, lon0 = points[0][0], points[0][1]
        self._lat0, self._lon0 = lat0, lon0
        self._ky = FEET_PER_DEGREE_LAT
        self._kx = FEET_PER_DEGREE_LAT * math.cos(math.radians(lat0))
        self.x = [(lon - lon0) * self._kx for _, lon, _ in points]
        self.y = [(lat - lat0) * self._ky for lat, _, _ in points]
        self.d = [dist for _, _, dist in points]

    def _segment(self, i: int, px: float, py: float) -> tuple[float, float]:
        """(offset, distance along the route) of the closest point on segment i."""

        ax, ay, bx, by = self.x[i], self.y[i], self.x[i + 1], self.y[i + 1]
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = (
            0.0
            if length_sq == 0
            else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
        )
        offset = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        return offset, self.d[i] + t * (self.d[i + 1] - self.d[i])

    def project(
        self,
        lat: float,
        lon: float,
        lo: float | None = None,
        hi: float | None = None,
        not_before: float | None = None,
    ) -> tuple[float, float] | None:
        """(distance along route, offset from route) within the window, or None.

        Where a route retraces itself (out and back on one street) several places are equally
        near. Among those within TIE_FT of the best, take the earliest that is not implausibly
        far behind `not_before` (the previous position); with no previous position, the earliest.
        """

        px, py = (lon - self._lon0) * self._kx, (lat - self._lat0) * self._ky
        last = len(self.d) - 2
        first_i = 0 if lo is None else max(0, bisect_right(self.d, lo) - 1)
        last_i = last if hi is None else min(last, bisect_left(self.d, hi))
        found = [self._segment(i, px, py) for i in range(first_i, last_i + 1)]
        if not found:
            return None
        best = min(off for off, _ in found)
        near = [a for off, a in found if off <= best + TIE_FT]
        if not_before is not None:
            forward = [a for a in near if a >= not_before]
            if forward:
                return min(forward), best
            return max(near), best  # every near candidate is behind: the closest to where it was
        return min(near), best


def _project_track(shape: Shape, fixes: Sequence[Fix]) -> list[tuple[float, float]]:
    """(timestamp, distance along route) for usable fixes, forced to move forward only."""

    track: list[tuple[float, float]] = []
    for fix in fixes:
        if not track:
            hit = shape.project(fix.lat, fix.lon)
        else:
            last_ts, last_d = track[-1]
            hit = shape.project(
                fix.lat,
                fix.lon,
                lo=last_d - BACK_TOL_FT,
                hi=last_d + MAX_SPEED_FT_S * (fix.ts - last_ts) + FORWARD_SLACK_FT,
                not_before=last_d - SMALL_BACK_FT,
            )
        if hit is None or hit[1] > OFFSET_LIMIT_FT:
            continue
        distance = hit[0] if not track else max(hit[0], track[-1][1])
        track.append((fix.ts, distance))
    return track


def _crossing(
    times: Sequence[float], dists: Sequence[float], threshold: float
) -> tuple[float | None, float | None, float | None, str]:
    """(lo, hi, estimate, method) for the first time the distance reaches `threshold`."""

    i = bisect_left(dists, threshold)
    if i >= len(times):
        return times[-1], None, None, "not_reached"
    if i == 0:
        return None, times[0], None, "before_first_fix"
    t0, d0, t1, d1 = times[i - 1], dists[i - 1], times[i], dists[i]
    est = t0 + (threshold - d0) / (d1 - d0) * (t1 - t0)
    return t0, t1, est, "interpolated" if t1 - t0 <= MAX_GAP_S else "wide_gap"


def enforce_order(visits: Sequence[StopVisit]) -> list[StopVisit]:
    """Make estimates non-decreasing along the trip, widening an interval rather than lying.

    Evidence from different sources (geometry, the feed's stop pointer, dwell reports) can
    disagree by a few seconds, so a later stop could come out earlier than the one before it. A
    bus cannot do that: raise the estimate to the previous one, and move `hi` up to match.
    """

    out: list[StopVisit] = []
    prev = float("-inf")
    for v in visits:
        arr_est, arr_hi = v.arr_est, v.arr_hi
        if arr_est is not None:
            if arr_est < prev:
                arr_est = prev
                if arr_hi is not None and arr_hi < arr_est:
                    arr_hi = arr_est
            prev = arr_est
        dep_est, dep_hi = v.dep_est, v.dep_hi
        if dep_est is not None:
            floor = arr_est if arr_est is not None else prev
            if dep_est < floor:
                dep_est = floor
                if dep_hi is not None and dep_hi < dep_est:
                    dep_hi = dep_est
            prev = max(prev, dep_est)
        out.append(replace(v, arr_est=arr_est, arr_hi=arr_hi, dep_est=dep_est, dep_hi=dep_hi))
    return out


def drop_stale_pointers(ordered: Sequence[Fix]) -> list[Fix]:
    """Blank the next-stop pointer on leading fixes that still carry the previous trip's value.

    Within a trip the pointer only moves forward. When a bus switches trips its first fix(es) can
    still name the old trip's next stop (measured 2026-10-16: seq 97 on a 77-stop trip), which
    would read as "every stop already served" and cap every arrival at that first fix. A leading
    fix whose pointer is greater than a later fix's pointer is therefore stale; its position is
    still used, only the pointer is dropped. Fixes after the first consistent one are untouched.
    """

    later_min: list[int | None] = [None] * len(ordered)
    low: int | None = None
    for i in range(len(ordered) - 1, -1, -1):
        later_min[i] = low
        p = ordered[i].next_seq
        if p is not None:
            low = p if low is None else min(low, p)
    out = list(ordered)
    for i, f in enumerate(ordered):
        p, m = f.next_seq, later_min[i]
        if p is None:
            continue
        if m is not None and p > m:
            out[i] = replace(f, next_seq=None)
        else:
            break
    return out


def infer_stop_visits(
    shape: Shape, fixes: Sequence[Fix], stops: Sequence[StopTarget]
) -> list[StopVisit]:
    """One StopVisit per stop, in the order given. Fixes need not be sorted."""

    ordered = drop_stale_pointers(sorted({f.ts: f for f in fixes}.values(), key=lambda f: f.ts))
    if not ordered:
        return [
            StopVisit(
                s.stop_sequence,
                s.stop_id,
                None,
                None,
                None,
                "no_fixes",
                None,
                None,
                None,
                "no_fixes",
                None,
                None,
                None,
            )  # fmt: skip
            for s in stops
        ]
    track = _project_track(shape, ordered)
    times = [t for t, _ in track]
    dists = [d for _, d in track]
    dist_at = dict(track)
    following = {s.stop_sequence: stops[i + 1].stop_sequence for i, s in enumerate(stops[:-1])}
    visits: list[StopVisit] = []
    for stop in stops:
        advance = next(
            (f.ts for f in ordered if f.next_seq is not None and f.next_seq > stop.stop_sequence),
            None,
        )
        dwell: list[float] = []
        nxt = following.get(stop.stop_sequence)
        if nxt is not None:
            for f in ordered:
                d = dist_at.get(f.ts)
                if (
                    f.stopped
                    and f.next_seq == nxt
                    and d is not None
                    and stop.dist - DWELL_BEHIND_FT <= d <= stop.dist + DWELL_AHEAD_FT
                ):
                    dwell.append(f.ts)
        dwell_first = min(dwell) if dwell else None
        dwell_last = max(dwell) if dwell else None

        if track:
            a_lo, a_hi, a_est, a_method = _crossing(
                times, dists, max(0.0, stop.dist - ARRIVE_TOL_FT)
            )
            d_lo, d_hi, d_est, d_method = _crossing(times, dists, stop.dist + DEPART_TOL_FT)
        else:
            a_lo = a_hi = a_est = d_lo = d_hi = d_est = None
            a_method = d_method = "no_geometry"
        geo_arr_est = a_est

        # Arrival: the pointer or a dwell report shows the bus had reached the stop by then.
        cap = min((t for t in (advance, dwell_first) if t is not None), default=None)
        if (
            a_method in ("interpolated", "wide_gap", "before_first_fix")
            and cap is not None
            and a_hi is not None
        ):
            a_hi = min(a_hi, cap)
            if a_lo is not None:
                a_lo = min(a_lo, a_hi)
            if a_est is not None:
                a_est = min(a_est, a_hi) if a_lo is None else min(max(a_est, a_lo), a_hi)
        elif a_method in ("no_geometry", "not_reached") and advance is not None:
            earlier = [f.ts for f in ordered if f.ts < advance]
            a_lo, a_hi = (earlier[-1] if earlier else None), advance
            a_est = (a_lo + a_hi) / 2 if a_lo is not None else None
            a_method = "next_stop_advance"

        # Departure: the bus cannot have left while it was still reported dwelling here.
        if dwell_last is not None and d_method in ("interpolated", "wide_gap", "before_first_fix"):
            d_lo = dwell_last if d_lo is None else max(d_lo, dwell_last)
            if d_est is not None:
                d_est = max(d_est, d_lo)
            if d_hi is not None:
                d_hi = max(d_hi, d_lo)
        visits.append(
            StopVisit(
                stop.stop_sequence,
                stop.stop_id,
                a_lo,
                a_hi,
                a_est,
                a_method,
                d_lo,
                d_hi,
                d_est,
                d_method,
                advance,
                dwell_first,
                dwell_last,
                geo_arr_est,
            )  # fmt: skip
        )
    return enforce_order(visits)
