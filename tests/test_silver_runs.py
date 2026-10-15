import datetime as dt

from transit_reliability.gtfs_static.load import trip_expansion_dates
from transit_reliability.silver_logic.runs import Run, extend_runs

A = ("07:55:17", 0)  # (predicted arrival, delay): stands in for the tuple of compared columns
B = ("07:56:04", 47)
C = ("07:55:46", 29)


def test_equal_consecutive_snapshots_collapse_into_one_run() -> None:
    runs = extend_runs(None, [(100, A), (160, A), (220, A)])
    assert runs == [Run(A, 100, 220, 3)]


def test_a_change_starts_a_new_run_and_returns_to_an_earlier_value_are_separate_runs() -> None:
    runs = extend_runs(None, [(100, A), (160, B), (220, A)])
    assert [(r.value, r.first_seen, r.last_seen, r.snapshots) for r in runs] == [
        (A, 100, 100, 1),
        (B, 160, 160, 1),
        (A, 220, 220, 1),
    ]


def test_snapshots_are_ordered_by_time_not_by_arrival_order() -> None:
    assert extend_runs(None, [(220, A), (100, A), (160, A)]) == [Run(A, 100, 220, 3)]


def test_a_new_batch_extends_the_open_run_from_the_previous_batch() -> None:
    first = extend_runs(None, [(100, A), (160, A)])
    assert first == [Run(A, 100, 160, 2)]
    second = extend_runs(first[-1], [(220, A), (280, A)])
    # Same first_seen, so a MERGE on (key, first_seen) updates the existing row.
    assert second == [Run(A, 100, 280, 4)]


def test_a_new_batch_that_changes_the_value_leaves_the_prior_run_alone() -> None:
    first = extend_runs(None, [(100, A), (160, A)])
    second = extend_runs(first[-1], [(220, B), (280, B)])
    assert second == [Run(B, 220, 280, 2)]  # the prior run is not returned: nothing changed


def test_snapshots_already_folded_in_are_ignored_so_overlapping_reads_are_harmless() -> None:
    first = extend_runs(None, [(100, A), (160, A)])
    again = extend_runs(first[-1], [(100, A), (160, A)])
    assert again == []
    mixed = extend_runs(first[-1], [(160, A), (220, A)])
    assert mixed == [Run(A, 100, 220, 3)]


def test_stitching_in_chunks_gives_the_same_answer_as_one_pass() -> None:
    stream = [
        (t, v) for t, v in zip(range(0, 600, 60), [A, A, B, B, B, C, A, A, A, B], strict=True)
    ]
    whole = extend_runs(None, stream)
    merged: dict[int, Run] = {}
    prior = None
    for chunk in (stream[:3], stream[3:7], stream[7:]):
        for run in extend_runs(prior, chunk):
            merged[run.first_seen] = run  # MERGE on first_seen: update or insert
        prior = merged[max(merged)]
    assert [merged[k] for k in sorted(merged)] == whole


def test_none_values_compare_equal_to_each_other() -> None:
    n = (None, None)
    assert extend_runs(None, [(1, n), (2, n)]) == [Run(n, 1, 2, 2)]


def test_trip_expansion_covers_realtime_dates_through_tomorrow() -> None:
    days = trip_expansion_dates(dt.date(2026, 10, 13), dt.date(2026, 10, 15), dt.date(2026, 10, 15))
    assert days[0] == dt.date(2026, 10, 13)
    assert days[-1] == dt.date(2026, 10, 16)  # tomorrow, so new service dates are never unknown


def test_trip_expansion_never_ends_before_the_last_realtime_date() -> None:
    days = trip_expansion_dates(dt.date(2026, 10, 13), dt.date(2026, 10, 20), dt.date(2026, 10, 15))
    assert days[-1] == dt.date(2026, 10, 20)


def test_a_long_silence_ends_the_run_even_when_the_value_is_unchanged() -> None:
    runs = extend_runs(None, [(0, A), (60, A), (60 + 4000, A)], max_gap=3600)
    assert runs == [Run(A, 0, 60, 2), Run(A, 4060, 4060, 1)]


def test_a_silence_of_exactly_the_limit_does_not_end_the_run() -> None:
    assert extend_runs(None, [(0, A), (3600, A)], max_gap=3600) == [Run(A, 0, 3600, 2)]


def test_the_silence_rule_gives_the_same_answer_however_the_work_is_batched() -> None:
    # A 4000 s silence placed so that each split point falls before, inside, or after it.
    stream = [(0, A), (60, A), (4060, A), (4120, A), (4180, B)]
    whole = extend_runs(None, stream, max_gap=3600)
    for cut in range(1, len(stream)):
        merged: dict[int, Run] = {}
        prior = None
        for chunk in (stream[:cut], stream[cut:]):
            for run in extend_runs(prior, chunk, max_gap=3600):
                merged[run.first_seen] = run
            prior = merged[max(merged)]
        assert [merged[k] for k in sorted(merged)] == whole, cut
