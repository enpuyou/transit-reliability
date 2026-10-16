from transit_reliability.reliability.lenses import LENSES, Lens, classify

COMMON = Lens("common", -60.0, 300.0)


def test_an_interval_fully_inside_the_window_is_definitely_on_time() -> None:
    assert classify(-30.0, 120.0, COMMON) == "on_time"


def test_the_boundaries_themselves_count_as_on_time() -> None:
    assert classify(-60.0, 300.0, COMMON) == "on_time"


def test_an_interval_entirely_past_the_late_limit_is_definitely_late() -> None:
    assert classify(301.0, 340.0, COMMON) == "late"


def test_an_interval_entirely_before_the_early_limit_is_definitely_early() -> None:
    assert classify(-200.0, -61.0, COMMON) == "early"


def test_an_interval_that_straddles_a_limit_is_uncertain_not_guessed() -> None:
    assert classify(280.0, 320.0, COMMON) == "uncertain"  # may or may not be over 5 minutes
    assert classify(-80.0, -40.0, COMMON) == "uncertain"
    assert classify(-100.0, 400.0, COMMON) == "uncertain"  # a wide gap says little


def test_a_missing_bound_is_not_classified() -> None:
    assert classify(None, 100.0, COMMON) is None
    assert classify(100.0, None, COMMON) is None


def test_a_stricter_lens_calls_the_same_visit_late() -> None:
    strict = next(lens for lens in LENSES if lens.name == "strict")
    assert classify(200.0, 220.0, COMMON) == "on_time"
    assert classify(200.0, 220.0, strict) == "late"


def test_the_headline_lens_is_the_common_window() -> None:
    from transit_reliability.reliability.lenses import headline

    lens = headline()
    assert (lens.name, lens.early_s, lens.late_s) == ("common", -60.0, 300.0)


def test_lenses_are_ordered_from_strict_to_lenient_and_named_uniquely() -> None:
    names = [lens.name for lens in LENSES]
    assert len(names) == len(set(names))
    late = [lens.late_s for lens in LENSES]
    assert late == sorted(late)
