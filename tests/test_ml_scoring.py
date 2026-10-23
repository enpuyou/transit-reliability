import pytest

from transit_reliability.ml.scoring import (
    BUCKETS,
    WMATA_WINDOWS,
    bootstrap_by_day,
    bucket_index,
    bucket_sql,
    interval_coverage,
    mae,
    mape,
    paired_gain_by_day,
    quantile,
    rmse,
    window_sql,
    within_window,
)


def test_window_edges_follow_the_wmata_table() -> None:
    # 0-3 min away: 1 minute early to 1 minute late
    assert within_window(2.0, -60) is True and within_window(2.0, 60) is True
    assert within_window(2.0, -61) is False and within_window(2.0, 61) is False
    # 3-6: 1.5 early, 2 late; 6-12: 2.5 early, 3.5 late; 12-30: 4 early, 6 late
    assert within_window(4.0, -90) is True and within_window(4.0, 121) is False
    assert within_window(8.0, -150) is True and within_window(8.0, 211) is False
    assert within_window(20.0, -240) is True and within_window(20.0, 361) is False


def test_early_is_never_judged_more_leniently_than_late_and_is_stricter_beyond_3_minutes() -> None:
    # the 0-3 minute window is symmetric (1 minute either way); every later window favours late
    # arrivals
    for i, (_, _, early, late) in enumerate(WMATA_WINDOWS):
        assert -early <= late
        if i > 0:
            assert -early < late


def test_boundaries_between_windows_and_unscorable_predictions() -> None:
    assert bucket_index(3.0) == 1 and bucket_index(2.999) == 0 and bucket_index(12.0) == 3
    assert bucket_index(30.0) is None and bucket_index(-0.1) is None
    assert within_window(45.0, 0) is None  # more than 30 minutes ahead is not scored


def test_sql_expressions_agree_with_the_python_rule() -> None:
    sql = window_sql("m", "e")
    for lo, hi, early, late in WMATA_WINDOWS:
        assert f"m >= {lo} AND m < {hi}" in sql and f"e BETWEEN {early} AND {late}" in sql
    assert all(name in bucket_sql("m") for name in BUCKETS)


def test_error_measures() -> None:
    errs = [-60.0, 0.0, 120.0]
    assert mae(errs) == pytest.approx(60.0)
    assert rmse(errs) == pytest.approx(((3600 + 0 + 14400) / 3) ** 0.5)
    assert mape([60.0, -30.0], [600.0, 300.0]) == pytest.approx(10.0)
    assert quantile([1, 2, 3, 4, 5], 0.5) == 3 and quantile([1, 2, 3, 4, 5], 0.9) == 4
    assert interval_coverage([0, 0, 0], [10, 10, 10], [5, 11, -1]) == pytest.approx(1 / 3)


def test_day_bootstrap_interval_contains_the_estimate_and_is_wider_with_fewer_days() -> None:
    many = {f"d{i}": (60.0 + (i % 3), 100.0) for i in range(12)}
    few = {k: many[k] for k in list(many)[:3]}
    est, lo, hi = bootstrap_by_day(many)
    assert lo <= est <= hi
    est2, lo2, hi2 = bootstrap_by_day(few)
    assert (hi2 - lo2) >= (hi - lo)


def test_paired_gain_says_when_a_difference_is_clear() -> None:
    a = {f"d{i}": (70.0, 100.0) for i in range(8)}
    b = {f"d{i}": (50.0, 100.0) for i in range(8)}
    diff, lo, hi = paired_gain_by_day(a, b)
    assert diff == pytest.approx(0.2) and lo > 0  # clearly better
    same = paired_gain_by_day(a, dict(a))
    assert same[0] == 0 and same[1] <= 0 <= same[2]  # no clear difference


def test_scoreboard_sql_scores_every_predictor_on_the_same_rows() -> None:
    from transit_reliability.ml.scoreboard import PREDICTORS, scoreboard_sql

    sql = scoreboard_sql("workspace.transit_ml.arrival_examples")
    assert "FROM workspace.transit_ml.arrival_examples" in sql and "UNION ALL" in sql
    for name in PREDICTORS:
        assert f"'{name}' AS predictor" in sql
    # one shared filter: the agency prediction must exist, be fresh and not canceled
    assert sql.count("WHERE hist_delay_mean_s IS NOT NULL AND hist_n >= 5 AND kcm_has_bus") == 1
    assert "service_date <= date_sub(" in sql  # incomplete days are not scored
    plus = scoreboard_sql("t", {"model": "model_pred"})
    assert "'model' AS predictor" in plus


def test_agency_prediction_age_can_never_reveal_the_future() -> None:
    import datetime as dt

    from transit_reliability.audit.catalog import ml_examples
    from transit_reliability.ml.arrival_examples import arrival_examples_sql

    assert "greatest(0, t.as_of - k.last_seen) AS kcm_age_s" in arrival_examples_sql(
        "transit", dt.date(2026, 10, 20)
    )
    names = {c.name for c in ml_examples("transit", "2026-10-20")}
    assert "ml_examples_leak_agency_age_from_future" in names
