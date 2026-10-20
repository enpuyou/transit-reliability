from transit_reliability.audit.catalog import all_checks
from transit_reliability.audit.core import (
    CheckSpec,
    epoch_gaps,
    evaluate,
    missing_nonempty_files,
    render,
    run_checks,
)
from transit_reliability.audit.runners import first_value


def test_a_value_inside_the_rule_passes_and_outside_fails() -> None:
    spec = CheckSpec("x", "SELECT 1", "why", minimum=1.0, maximum=3.0)
    assert evaluate(spec, 2.0).passed
    assert evaluate(spec, 1.0).passed and evaluate(spec, 3.0).passed
    assert not evaluate(spec, 0.99).passed and not evaluate(spec, 3.01).passed


def test_a_missing_value_fails() -> None:
    assert not evaluate(CheckSpec("x", "SELECT 1", "why", maximum=0.0), None).passed


def test_a_result_carries_name_value_rule_and_sql_for_rerunning_by_hand() -> None:
    result = evaluate(CheckSpec("dups", "SELECT 7", "unique", maximum=0.0), 7.0)
    assert (result.name, result.value, result.sql) == ("dups", 7.0, "SELECT 7")
    assert result.rule == "value <= 0.0" and not result.passed


def test_a_query_that_raises_is_a_failed_check_and_the_rest_still_run() -> None:
    specs = [
        CheckSpec("bad", "BOOM", "w", maximum=0.0),
        CheckSpec("good", "SELECT 0", "w", maximum=0.0),
    ]

    def runner(sql: str) -> float | None:
        if sql == "BOOM":
            raise RuntimeError("warehouse down")
        return 0.0

    bad, good = run_checks(specs, runner)
    assert not bad.passed and bad.error == "warehouse down" and good.passed


def test_render_marks_failures_and_counts() -> None:
    results = run_checks(
        [CheckSpec("a", "A", "w", maximum=0.0), CheckSpec("b", "B", "w", maximum=0.0)],
        lambda sql: 0.0 if sql == "A" else 5.0,
    )
    text = render(results)
    assert "ok   a" in text and "FAIL b" in text and "1 passed, 1 failed, 0 warnings" in text


def test_missing_files_are_ignored_only_when_they_are_empty_feeds() -> None:
    raw = {"1.pb": 60_000, "2.pb": 15, "3.pb": 58_000, "4.pb": 16}
    assert missing_nonempty_files(raw, ["1.pb"]) == ["3.pb", "4.pb"]
    assert missing_nonempty_files(raw, ["1.pb", "3.pb", "4.pb"]) == []


def test_epoch_gaps_lists_only_gaps_longer_than_the_limit() -> None:
    epochs = [0, 20_000, 40_000, 400_000, 420_000]
    assert epoch_gaps(epochs, 180.0) == [(400_000, 360.0)]
    assert epoch_gaps([5_000, 1_000], 1.0) == [(5_000, 4.0)]


def test_first_value_reads_the_statement_api_shape() -> None:
    assert first_value({"result": {"data_array": [["12.5"]]}}) == 12.5
    assert first_value({"result": {"data_array": [[None]]}}) is None
    assert first_value({"result": {}}) is None


def test_every_catalog_check_is_a_named_single_select_and_names_are_unique() -> None:
    specs = all_checks("transit", "2026-10-16")
    assert len({s.name for s in specs}) == len(specs) > 20
    for s in specs:
        # read-only, one statement (a CTE is allowed)
        assert s.sql.lstrip().upper().startswith(("SELECT", "WITH")), s.name
        assert s.minimum is not None or s.maximum is not None, s.name
        for word in ("INSERT", "DELETE", "DROP", "MERGE", "UPDATE", "ALTER"):
            assert f" {word} " not in s.sql.upper(), (s.name, word)
        assert "workspace.transit_" in s.sql
        assert "2026-10-16" in s.sql or s.name.startswith(("silver_", "quarantine_")), (
            s.name
        )  # date-bound checks must use the date given


def test_the_catalog_follows_the_schema_prefix() -> None:
    assert all("workspace.dev_transit_" in s.sql for s in all_checks("dev_transit", "2026-10-16"))
