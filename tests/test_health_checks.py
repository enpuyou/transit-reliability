from transit_reliability.reliability.health import Check, failures


def test_a_value_inside_its_bounds_passes() -> None:
    assert failures([Check("coverage", 0.85, minimum=0.5)]) == []
    assert failures([Check("lag_hours", 1.0, maximum=3.0)]) == []
    assert failures([Check("band", 5.0, minimum=1.0, maximum=9.0)]) == []


def test_a_value_outside_its_bounds_fails_with_the_check_name_and_numbers() -> None:
    messages = failures(
        [Check("coverage", 0.003, minimum=0.5), Check("lag_hours", 40.0, maximum=3.0)]
    )
    assert len(messages) == 2
    assert "coverage" in messages[0] and "0.003" in messages[0] and "0.5" in messages[0]
    assert "lag_hours" in messages[1]


def test_a_missing_value_is_a_failure_not_a_pass() -> None:
    # no data at all must never look healthy
    assert failures([Check("coverage", None, minimum=0.5)]) == ["coverage: no value"]


def test_the_boundaries_themselves_pass() -> None:
    assert failures([Check("a", 0.5, minimum=0.5), Check("b", 3.0, maximum=3.0)]) == []


def test_a_zero_violations_check_fails_on_any_violation() -> None:
    assert failures([Check("violations", 0.0, maximum=0.0)]) == []
    assert failures([Check("violations", 1.0, maximum=0.0)]) != []
