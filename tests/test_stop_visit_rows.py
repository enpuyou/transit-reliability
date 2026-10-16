import math

from transit_reliability.arrivals.infer import FEET_PER_DEGREE_LAT
from transit_reliability.arrivals.trip_rows import stop_visit_rows

LAT0, LON0 = 47.6, -122.3
KX = FEET_PER_DEGREE_LAT * math.cos(math.radians(LAT0))


def at(x: float) -> tuple[float, float]:
    return LAT0, LON0 + x / KX


SHAPE = [(*at(x), float(x)) for x in range(0, 10_001, 1_000)]
# (stop_sequence, stop_id, dist, scheduled arrival epoch, scheduled departure epoch, timepoint)
STOPS = [
    (1, "A", 1000.0, 1_000.0, 1_000.0, 1),
    (2, "B", 3000.0, 1_100.0, 1_100.0, 0),
    (3, "C", 6000.0, 1_200.0, 1_205.0, 0),
]


def fixes_at_30_ft_per_s(
    start: float = 1_000.0 - 33.0,
) -> list[tuple[float, float, float, int | None, bool]]:
    """(ts, lat, lon, next_seq, stopped): the bus is at x=0 at `start` and drives 30 ft/s."""

    out: list[tuple[float, float, float, int | None, bool]] = []
    for i in range(0, 18):
        ts = start + 20.0 * i
        out.append((ts, *at(30.0 * 20.0 * i), None, False))
    return out


def test_each_scheduled_stop_gets_a_row_with_delay_against_the_schedule() -> None:
    rows = stop_visit_rows(SHAPE, fixes_at_30_ft_per_s(), STOPS)
    assert [r["stop_sequence"] for r in rows] == [1, 2, 3]
    first = rows[0]
    # stop A (x=1000): arrival threshold 960 ft -> 32 s after the start fix at t=967 -> t=999
    assert first["arr_method"] == "interpolated"
    assert abs(first["arr_est"] - 999.0) < 0.1
    assert abs(first["arr_delay_s"] - (999.0 - 1000.0)) < 0.1  # one second early
    assert first["sched_arr"] == 1_000.0 and first["timepoint"] == 1


def test_a_trip_with_no_fixes_still_yields_a_row_per_stop() -> None:
    rows = stop_visit_rows(SHAPE, [], STOPS)
    assert len(rows) == 3
    assert all(
        r["arr_method"] == "no_fixes" and r["arr_est"] is None and r["arr_delay_s"] is None
        for r in rows
    )
    assert rows[1]["sched_arr"] == 1_100.0  # the schedule is kept, so missing trips are countable


def test_a_trip_with_no_usable_shape_is_labelled_not_guessed() -> None:
    rows = stop_visit_rows([], fixes_at_30_ft_per_s(), STOPS)
    assert all(r["arr_method"] == "no_shape" and r["arr_est"] is None for r in rows)
    assert len(rows) == 3


def test_departure_delay_uses_the_scheduled_departure() -> None:
    rows = stop_visit_rows(SHAPE, fixes_at_30_ft_per_s(), STOPS)
    third = rows[2]
    assert third["sched_dep"] == 1_205.0
    assert third["dep_est"] is not None
    assert abs(third["dep_delay_s"] - (third["dep_est"] - 1_205.0)) < 1e-9
