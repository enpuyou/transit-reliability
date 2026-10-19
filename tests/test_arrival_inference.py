import math
from dataclasses import replace

from transit_reliability.arrivals.infer import (
    ARRIVE_TOL_FT,
    DEPART_TOL_FT,
    FEET_PER_DEGREE_LAT,
    Fix,
    Shape,
    StopTarget,
    StopVisit,
    infer_stop_visits,
)

LAT0, LON0 = 47.6, -122.3
KX = FEET_PER_DEGREE_LAT * math.cos(math.radians(LAT0))
SPEED = 30.0


def latlon(x_ft: float, y_ft: float = 0.0) -> tuple[float, float]:
    """Feet east and north of the test origin as (lat, lon)."""

    return LAT0 + y_ft / FEET_PER_DEGREE_LAT, LON0 + x_ft / KX


def line_shape(length: int = 10_000, step: int = 1_000) -> Shape:
    return Shape([(*latlon(x), float(x)) for x in range(0, length + 1, step)])


STOPS = [
    StopTarget(1, "A", 1000.0),
    StopTarget(2, "B", 3000.0),
    StopTarget(3, "C", 6000.0),
    StopTarget(4, "D", 9000.0),
]


def next_stop_for(x: float) -> int:
    """The feed's pointer: the sequence of the first stop the bus has not yet passed."""

    return next((s.stop_sequence for s in STOPS if x < s.dist), 5)


def drive(
    every: float = 20.0,
    start_x: float = 0.0,
    end_t: float = 330.0,
    skip: tuple[float, float] | None = None,
    hold: tuple[float, float, float] | None = None,
    pointer: bool = False,
) -> list[Fix]:
    """Fixes of a bus on the line. `hold=(x, t_start, t_end)` parks it at x between those times."""

    fixes: list[Fix] = []
    t = 0.0
    while t <= end_t + 1e-9:
        x = start_x + SPEED * t
        parked = False
        if hold and hold[1] <= t <= hold[2]:
            x, parked = hold[0], True
        elif hold and t > hold[2]:
            x = hold[0] + SPEED * (t - hold[2])
        if not (skip and skip[0] < t < skip[1]):
            nxt = next_stop_for(x if not parked else x + 1.0) if pointer else None
            fixes.append(Fix(t, *latlon(x), next_seq=nxt, stopped=parked))
        t += every
    return fixes


def arrival_truth(dist: float) -> float:
    return (dist - ARRIVE_TOL_FT) / SPEED


def departure_truth(dist: float) -> float:
    return (dist + DEPART_TOL_FT) / SPEED


def by_seq(visits: list[StopVisit]) -> dict[int, StopVisit]:
    return {v.stop_sequence: v for v in visits}


def arr(visit: StopVisit) -> float:
    assert visit.arr_est is not None
    return visit.arr_est


def dep(visit: StopVisit) -> float:
    assert visit.dep_est is not None
    return visit.dep_est


def test_steady_travel_recovers_each_arrival_and_brackets_it_with_the_fixes() -> None:
    result = by_seq(infer_stop_visits(line_shape(), drive(), STOPS))
    for stop in STOPS:
        v = result[stop.stop_sequence]
        assert v.arr_method == "interpolated"
        assert abs(arr(v) - arrival_truth(stop.dist)) < 0.05
        assert v.arr_lo is not None and v.arr_hi is not None and v.arr_lo <= arr(v) <= v.arr_hi
        assert v.arr_hi - v.arr_lo == 20.0
        assert abs(dep(v) - departure_truth(stop.dist)) < 0.05


def test_a_layover_does_not_delay_arrival_and_departure_waits_for_it_to_end() -> None:
    # parked at stop B (x=3000) from t=100 to t=700, then continues
    fixes = drive(end_t=1000, hold=(3000.0, 100.0, 700.0))
    result = by_seq(infer_stop_visits(line_shape(), fixes, STOPS))
    assert abs(arr(result[2]) - arrival_truth(3000)) < 0.05
    assert (
        abs(dep(result[2]) - (700 + DEPART_TOL_FT / SPEED)) < 0.05
    )  # leaves when the layover ends
    assert abs(arr(result[3]) - (700 + (6000 - ARRIVE_TOL_FT - 3000) / SPEED)) < 0.1


def test_a_missing_stretch_gives_a_wide_labelled_interval_not_a_false_precise_time() -> None:
    result = by_seq(infer_stop_visits(line_shape(), drive(skip=(80.0, 300.0)), STOPS))
    b, c = result[2], result[3]
    assert b.arr_method == "wide_gap" and c.arr_method == "wide_gap"
    assert b.arr_lo == 80.0 and b.arr_hi == 300.0
    assert abs(arr(b) - arrival_truth(3000)) < 0.1  # still right on a straight road, but labelled


def test_a_bus_first_seen_beyond_a_stop_has_no_arrival_for_it() -> None:
    result = by_seq(infer_stop_visits(line_shape(), drive(start_x=3500.0, end_t=200), STOPS))
    assert result[1].arr_method == "before_first_fix" and result[1].arr_est is None
    assert result[1].arr_lo is None
    assert result[2].arr_method == "before_first_fix"
    assert result[3].arr_method == "interpolated"


def test_a_bus_last_seen_before_a_stop_has_no_arrival_for_it() -> None:
    result = by_seq(infer_stop_visits(line_shape(), drive(end_t=170), STOPS))  # ends near x=5100
    assert result[2].arr_method == "interpolated"
    assert result[3].arr_method == "not_reached" and result[3].arr_est is None
    assert result[4].arr_method == "not_reached"
    assert result[2].dep_method == "interpolated"
    assert result[3].dep_method == "not_reached"


def test_gps_noise_moves_estimates_only_a_little() -> None:
    fixes = []
    for i, f in enumerate(drive()):
        along = (-1) ** i * 18 * ((i % 3) + 1) / 3
        side = (-1) ** (i // 2) * 15
        fixes.append(Fix(f.ts, *latlon(SPEED * f.ts + along, side)))
    result = by_seq(infer_stop_visits(line_shape(), fixes, STOPS))
    for stop in STOPS:
        assert abs(arr(result[stop.stop_sequence]) - arrival_truth(stop.dist)) < 3.0


def test_a_fix_far_off_the_route_is_ignored() -> None:
    clean = infer_stop_visits(line_shape(), drive(), STOPS)
    fixes = drive()
    fixes[6] = Fix(fixes[6].ts, *latlon(SPEED * fixes[6].ts, 2000.0))  # 2000 ft off the road
    noisy = infer_stop_visits(line_shape(), fixes, STOPS)
    for a, b in zip(clean, noisy, strict=True):
        assert abs(arr(a) - arr(b)) < 8.0


def test_a_fix_that_jumps_backward_does_not_move_a_passed_stop() -> None:
    fixes = drive()
    fixes[7] = Fix(fixes[7].ts, *latlon(SPEED * fixes[7].ts - 150.0))  # 150 ft behind
    result = by_seq(infer_stop_visits(line_shape(), fixes, STOPS))
    for stop in STOPS:
        assert abs(arr(result[stop.stop_sequence]) - arrival_truth(stop.dist)) < 6.0


def _out_and_back(lane_offset: float) -> tuple[Shape, list[Fix]]:
    out = [(*latlon(x), float(x)) for x in range(0, 5001, 500)]
    back = [(*latlon(5000 - x, lane_offset), float(5000 + x)) for x in range(500, 5001, 500)]
    fixes = []
    t = 0.0
    while t <= 330.0:
        dist = SPEED * t
        fixes.append(
            Fix(t, *(latlon(dist) if dist <= 5000 else latlon(10_000 - dist, lane_offset)))
        )
        t += 20.0
    return Shape(out + back), fixes


def test_an_out_and_back_route_keeps_the_return_leg_on_the_return_distance() -> None:
    for lane in (0.0, 35.0):  # exact retrace, and a return leg one lane away
        shape, fixes = _out_and_back(lane)
        stops = [StopTarget(1, "B", 4000.0), StopTarget(2, "C", 6000.0), StopTarget(3, "D", 9000.0)]
        result = by_seq(infer_stop_visits(shape, fixes, stops))
        for stop in stops:
            assert result[stop.stop_sequence].arr_method == "interpolated"
            assert abs(arr(result[stop.stop_sequence]) - arrival_truth(stop.dist)) < 0.5


def test_the_next_stop_pointer_is_kept_as_evidence_for_when_a_stop_was_passed() -> None:
    result = by_seq(infer_stop_visits(line_shape(), drive(pointer=True), STOPS))
    b = result[2]  # B is at x=3000; the pointer moves to stop 3 at the first fix with x >= 3000
    assert b.advance_ts == 100.0
    assert b.arr_hi is not None and b.arr_hi <= 100.0
    assert result[4].advance_ts is not None  # pointer reaches 5 (past the last stop)


def test_dwell_evidence_caps_arrival_and_floors_departure() -> None:
    # parked at B from t=100 to 700; the feed says STOPPED_AT with the next stop being C (seq 3)
    fixes = drive(end_t=1000, hold=(3000.0, 100.0, 700.0), pointer=True)
    result = by_seq(infer_stop_visits(line_shape(), fixes, STOPS))
    b = result[2]
    assert b.dwell_first == 100.0 and b.dwell_last == 700.0
    assert b.arr_hi is not None and b.arr_hi <= 100.0
    assert b.dep_lo is not None and b.dep_lo >= 700.0


def test_stopped_reports_far_from_the_stop_are_not_dwell_evidence() -> None:
    # stopped at a red light 900 ft short of stop B but the feed names B as next: not a dwell at A
    fixes = [
        Fix(t, *latlon(SPEED * t if t < 70 else 2100.0), next_seq=2, stopped=t >= 70)
        for t in range(0, 200, 20)
    ]
    result = by_seq(
        infer_stop_visits(
            line_shape(),
            [Fix(float(f.ts), f.lat, f.lon, f.next_seq, f.stopped) for f in fixes],
            STOPS,
        )
    )
    assert result[1].dwell_first is None  # it was 1000+ ft past stop A when stopped


def test_the_pointer_alone_gives_an_interval_when_the_route_geometry_cannot() -> None:
    # all fixes lie on a different road far from the shape; the pointer moves from 3 to 4 at t=60
    fixes = [
        Fix(float(t), *latlon(1000.0 + 20 * t, 3000.0), next_seq=3 if t < 60 else 4)
        for t in (0, 20, 40, 60, 80)
    ]
    result = by_seq(infer_stop_visits(line_shape(), fixes, STOPS))
    c = result[3]
    assert c.arr_method == "next_stop_advance" and c.arr_lo == 40.0 and c.arr_hi == 60.0
    assert c.arr_est == 50.0


def test_no_fixes_means_no_visits() -> None:
    result = infer_stop_visits(line_shape(), [], STOPS)
    assert all(v.arr_est is None and v.arr_method == "no_fixes" for v in result)


def _visit(
    seq: int, arr_est: float | None, arr_hi: float | None = None, dep_est: float | None = None
) -> StopVisit:
    return StopVisit(
        seq, str(seq), None, arr_hi, arr_est, "x", None, None, dep_est, "x", None, None, None
    )


def test_estimates_never_run_backward_along_a_trip() -> None:
    from transit_reliability.arrivals.infer import enforce_order

    out = enforce_order([_visit(1, 100.0), _visit(2, 90.0, arr_hi=95.0), _visit(3, 200.0)])
    assert [v.arr_est for v in out] == [100.0, 100.0, 200.0]
    assert out[1].arr_hi == 100.0  # the interval is widened to stay consistent, not left impossible


def test_a_departure_is_never_before_the_arrival_at_the_same_stop() -> None:
    from transit_reliability.arrivals.infer import enforce_order

    out = enforce_order([_visit(1, 100.0, dep_est=90.0), _visit(2, 150.0, dep_est=170.0)])
    assert out[0].dep_est == 100.0 and out[1].dep_est == 170.0


def test_missing_estimates_do_not_break_the_ordering_pass() -> None:
    from transit_reliability.arrivals.infer import enforce_order

    out = enforce_order([_visit(1, 100.0), _visit(2, None), _visit(3, 120.0)])
    assert [v.arr_est for v in out] == [100.0, None, 120.0]


def test_a_stale_pointer_on_the_first_fix_does_not_cap_every_arrival() -> None:
    # the bus has just switched trips: its first fix still carries the previous trip's pointer
    fixes = drive(pointer=True)
    stale = [replace(fixes[0], next_seq=97), *fixes[1:]]
    clean = by_seq(infer_stop_visits(line_shape(), fixes, STOPS))
    result = by_seq(infer_stop_visits(line_shape(), stale, STOPS))
    for stop in STOPS:
        v = result[stop.stop_sequence]
        assert v.arr_method == "interpolated"
        assert v.arr_hi is not None and v.arr_lo is not None and v.arr_hi - v.arr_lo <= 20.0
        assert abs(arr(v) - arr(clean[stop.stop_sequence])) < 0.05
        assert v.advance_ts == clean[stop.stop_sequence].advance_ts


def test_a_pointer_that_only_ever_moves_forward_is_left_alone() -> None:
    fixes = drive(pointer=True)
    assert infer_stop_visits(line_shape(), fixes, STOPS) == infer_stop_visits(
        line_shape(), [replace(f) for f in fixes], STOPS
    )
