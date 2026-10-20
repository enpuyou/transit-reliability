from transit_reliability.reliability.lenses import LENSES, Lens, classify, classify_rider

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


def rider(
    arr: tuple[float | None, float | None], dep: tuple[float | None, float | None], **kw: bool
) -> str | None:
    return classify_rider(
        arr, dep, first_stop=kw.get("first", False), last_stop=kw.get("last", False)
    )


def test_a_bus_that_arrives_early_but_leaves_on_time_is_on_time_for_a_rider() -> None:
    # arrived 3 minutes early and held until the scheduled departure: normal practice
    assert rider((-190.0, -170.0), (-10.0, 20.0)) == "on_time"


def test_a_bus_that_leaves_early_is_early_even_if_it_arrived_on_time() -> None:
    assert rider((-20.0, 0.0), (-130.0, -100.0)) == "early"


def test_a_bus_that_arrives_late_is_late_even_if_it_left_within_the_window() -> None:
    # the intervals can disagree slightly; the arrival decides lateness
    assert rider((320.0, 340.0), (250.0, 290.0)) == "late"


def test_the_first_stop_is_judged_on_the_departure_because_the_bus_is_parked() -> None:
    assert rider((None, None), (330.0, 360.0), first=True) == "late"
    assert rider((None, None), (-10.0, 20.0), first=True) == "on_time"
    assert rider((None, None), (-90.0, -70.0), first=True) == "early"


def test_the_last_stop_and_unmeasured_timepoints_are_not_scored() -> None:
    assert rider((10.0, 30.0), (None, None), last=True) is None
    assert rider((None, None), (None, None)) is None


def test_either_measured_time_can_certify_on_time() -> None:
    assert rider((-20.0, 40.0), (None, None)) == "on_time"  # only the arrival was measured
    assert rider((None, None), (-20.0, 40.0)) == "on_time"  # only the departure was measured


def test_an_interval_straddling_a_limit_is_uncertain_not_guessed() -> None:
    assert rider((280.0, 320.0), (None, None)) == "uncertain"  # might be over 5 minutes late
    assert rider((None, None), (-80.0, -40.0)) == "uncertain"  # might have left over a minute early
    assert rider((None, None), (-100.0, 10.0)) == "uncertain"  # may or may not have left early


def test_a_one_sided_departure_bound_can_prove_late_at_the_first_stop_but_never_on_time_alone() -> (
    None
):
    # still at the stop 8 minutes after its scheduled departure: it left at least that late
    assert rider((None, None), (480.0, None), first=True) == "late"
    # still there at +2 minutes: not early, but we cannot say it was not late: uncertain
    assert rider((None, None), (120.0, None), first=True) == "uncertain"


def test_a_one_sided_bound_still_certifies_not_early_when_the_other_side_is_known() -> None:
    # arrival known within the window; the departure is only known to be after -20 s
    assert rider((-30.0, 100.0), (-20.0, None)) == "on_time"
