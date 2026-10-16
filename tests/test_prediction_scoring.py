from transit_reliability.reliability.prediction import (
    MAX_STALE_S,
    ObservedVisit,
    PredRun,
    score_trip,
)

MIN = 60.0


def visit(seq: int, sched: float, actual: float, delay: float | None = None) -> ObservedVisit:
    return ObservedVisit(seq, sched, actual, actual - sched if delay is None else delay)


def run(
    seq: int,
    first: float,
    last: float,
    pred: float | None,
    assigned: bool = True,
    canceled: bool = False,
) -> PredRun:
    return PredRun(seq, first, last, pred, assigned, canceled)


# a bus reaches stop 3 at t=1000, scheduled for t=900 (100 s late); seen at stop 1 earlier
TARGET = visit(3, sched=900.0, actual=1000.0)
EARLIER = visit(
    1, sched=100.0, actual=130.0
)  # known by every 5-minute horizon (t=700), not a 15-minute one


def test_the_prediction_is_the_latest_one_published_at_or_before_the_horizon() -> None:
    horizon_s = 5 * MIN  # as-of time = 1000 - 300 = 700
    runs = [
        run(3, 0.0, 600.0, 880.0),  # old prediction
        run(3, 640.0, 690.0, 960.0),  # the latest at t=700
        run(3, 720.0, 800.0, 990.0),  # published after t: must not be used (no future)
    ]
    rows = score_trip([EARLIER, TARGET], runs, first_fix=0.0, horizons=(5,))
    rows = [r for r in rows if r.stop_sequence == 3]
    assert len(rows) == 1
    assert rows[0].as_of == 1000.0 - horizon_s
    assert rows[0].kcm_error == 960.0 - 1000.0  # predicted 40 s early


def test_errors_are_predicted_minus_actual_so_positive_means_predicted_too_late() -> None:
    rows = score_trip(
        [EARLIER, TARGET], [run(3, 600.0, 690.0, 1100.0)], first_fix=0.0, horizons=(5,)
    )
    assert rows[0].kcm_error == 100.0
    assert rows[0].sched_error == 900.0 - 1000.0


def test_nothing_is_scored_before_the_bus_has_been_seen_on_the_trip() -> None:
    runs = [run(3, 600.0, 690.0, 980.0)]
    assert score_trip([TARGET], runs, first_fix=800.0, horizons=(5,)) == []  # first fix after t=700


def test_a_prediction_with_no_bus_assigned_is_not_a_prediction() -> None:
    runs = [run(3, 600.0, 690.0, 900.0, assigned=False)]
    assert score_trip([TARGET], runs, first_fix=0.0, horizons=(5,)) == []


def test_canceled_trips_and_missing_arrival_times_are_skipped() -> None:
    assert score_trip([TARGET], [run(3, 600.0, 690.0, 980.0, canceled=True)], 0.0, (5,)) == []
    assert score_trip([TARGET], [run(3, 600.0, 690.0, None)], 0.0, (5,)) == []


def test_a_prediction_that_went_quiet_too_long_before_the_horizon_is_not_used() -> None:
    last = 700.0 - MAX_STALE_S - 1.0
    assert score_trip([EARLIER, TARGET], [run(3, 0.0, last, 980.0)], 0.0, (5,)) == []
    assert (
        len(score_trip([EARLIER, TARGET], [run(3, 0.0, 700.0 - MAX_STALE_S, 980.0)], 0.0, (5,)))
        == 1
    )


def test_persistence_carries_the_most_recent_observed_delay_forward() -> None:
    earlier = [
        visit(1, sched=500.0, actual=530.0),
        visit(2, sched=600.0, actual=640.0),
    ]  # +30, +40 s
    runs = [run(3, 600.0, 690.0, 980.0)]
    rows = score_trip([*earlier, TARGET], runs, first_fix=0.0, horizons=(5,))
    row = next(r for r in rows if r.stop_sequence == 3)
    # at t=700 the latest observed stop is 2 (actual 640), delay 40 -> predicts 900 + 40 = 940
    assert row.persistence_error == 940.0 - 1000.0


def test_a_stop_reached_after_the_horizon_cannot_feed_persistence() -> None:
    late_stop = visit(2, sched=600.0, actual=850.0)  # observed after t=700
    rows = score_trip([late_stop, TARGET], [run(3, 600.0, 690.0, 980.0)], 0.0, (5,))
    assert [
        r for r in rows if r.stop_sequence == 3
    ] == []  # no delay known yet at t: not comparable


def test_all_predictors_are_scored_on_the_same_rows_and_each_horizon_separately() -> None:
    earlier = [visit(1, sched=100.0, actual=50.0)]  # known even by the 15-minute horizon (t=100)
    runs = [run(3, 0.0, 2000.0, 990.0)]
    rows = score_trip([*earlier, TARGET], runs, first_fix=0.0, horizons=(5, 15))
    assert sorted((r.stop_sequence, r.horizon_min) for r in rows) == [(3, 5), (3, 15)]
    assert all(r.persistence_error is not None for r in rows)
