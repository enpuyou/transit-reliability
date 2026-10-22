import datetime as dt

from transit_reliability.audit.core import CheckSpec, evaluate
from transit_reliability.monitoring.daily import complete_day, drift_checks
from transit_reliability.monitoring.hourly import hourly_checks
from transit_reliability.monitoring.run import blocking_failures, result_rows, specs_for
from transit_reliability.monitoring.watchdog import (
    job_finding,
    landing_finding,
    newest_epoch_ms,
    silver_finding,
)

NOW = dt.datetime(2026, 10, 19, 23, 10, tzinfo=dt.UTC)


def ms(minutes_ago: float, base: dt.datetime = NOW) -> int:
    return int((base - dt.timedelta(minutes=minutes_ago)).timestamp() * 1000)


def run(minutes_ago: float, result: str | None = "SUCCESS", base: dt.datetime = NOW) -> dict:  # type: ignore[type-arg]
    done = result is not None
    return {
        "start_time": ms(minutes_ago, base),
        "state": {"life_cycle_state": "TERMINATED" if done else "RUNNING", "result_state": result},
    }


def test_the_newest_collector_file_is_found_from_its_epoch_name() -> None:
    names = ["1000.pb", "1792449250117.pb", "x-smoke.pb", "latest.sha"]
    assert newest_epoch_ms(names) == 1792449250117
    assert newest_epoch_ms(["x-smoke.pb"]) is None


def test_a_stale_landing_folder_fails_and_a_fresh_one_passes() -> None:
    fresh = landing_finding("vehicle_positions", [f"{ms(2)}.pb"], NOW)
    stale = landing_finding("vehicle_positions", [f"{ms(45)}.pb"], NOW)
    assert fresh.ok and not stale.ok and "45" in stale.detail
    assert not landing_finding("alerts", [], NOW).ok


def test_a_job_that_never_fired_fails_even_though_nothing_failed() -> None:
    # the 2026-10-19 case: the newest run succeeded, but it was five hours ago
    assert not job_finding("ingest", [run(330)], NOW, 75).ok
    assert job_finding("ingest", [run(10), run(40)], NOW, 75).ok


def test_the_newest_finished_run_must_have_succeeded() -> None:
    assert not job_finding("monitor", [run(5, "FAILED"), run(65)], NOW, 90).ok
    # a run still in progress is judged by the finished run before it
    assert job_finding("ingest", [run(2, None), run(32)], NOW, 75).ok
    assert not job_finding("ingest", [run(2, None), run(32, "FAILED")], NOW, 75).ok
    assert not job_finding("ingest", [], NOW, 75).ok


def test_the_daily_silver_run_is_only_required_once_it_is_due() -> None:
    early = dt.datetime(2026, 10, 19, 9, 0, tzinfo=dt.UTC)
    assert silver_finding([run(22 * 60, base=early)], early).ok  # yesterday's run is fine at 09:00
    late = dt.datetime(2026, 10, 19, 14, 0, tzinfo=dt.UTC)
    assert not silver_finding([run(22 * 60, base=late)], late).ok  # nothing today by 14:00
    assert silver_finding([run(2 * 60, base=late)], late).ok
    assert not silver_finding([run(2 * 60, "FAILED", base=late)], late).ok


def test_hourly_checks_are_single_statements_with_a_rule_and_unique_names() -> None:
    checks = hourly_checks("transit")
    names = [c.name for c in checks]
    assert len(names) == len(set(names))
    for c in checks:
        assert c.sql.lstrip().upper().startswith(("SELECT", "WITH")) and ";" not in c.sql
        assert c.minimum is not None or c.maximum is not None
        assert "workspace.transit_" in c.sql or "/Volumes/workspace/transit_landing/" in c.sql
    assert {"days_of_schedule_left", "scheduled_trips_today"} <= set(names)
    assert (
        "scheduled_trips_tomorrow" not in names
    )  # false alarm 00:00 to 04:45 Seattle (2026-10-20)


def test_drift_checks_use_only_the_given_day_and_the_days_before() -> None:
    for c in drift_checks("transit", "2026-10-16"):
        assert "2026-10-16" in c.sql and c.name


def test_the_daily_suite_checks_the_newest_complete_day() -> None:
    today = dt.date(2026, 10, 19)
    assert complete_day(today) == dt.date(2026, 10, 17)
    specs, day = specs_for("daily", "transit", today)
    assert day == dt.date(2026, 10, 17) and len(specs) > 28


def test_a_warning_is_recorded_but_does_not_block_while_a_failure_does() -> None:
    warn = evaluate(CheckSpec("w", "SELECT 1", "", maximum=0, severity="warn"), 1.0)
    fail = evaluate(CheckSpec("f", "SELECT 1", "", maximum=0), 1.0)
    missing = evaluate(CheckSpec("m", "SELECT 1", "", maximum=0), None, error="boom")
    assert [r.status for r in (warn, fail, missing)] == ["warn", "fail", "fail"]
    problems = blocking_failures([warn, fail, missing])
    assert len(problems) == 2 and "boom" in problems[1]


def test_result_rows_carry_everything_needed_to_rerun_a_check_by_hand() -> None:
    result = evaluate(CheckSpec("c", "SELECT 2", "why", minimum=1), 2.0)
    (row,) = result_rows([result], "hourly", dt.datetime(2026, 10, 19, 23, 12), None)
    assert row[2:4] == ("c", "pass") and row[9] == "SELECT 2" and row[8] == "why"


def test_proof_report_helpers_show_pacific_wall_clock_and_real_distances() -> None:
    from transit_reliability.monitoring.proof import evenly_spaced, feet_between, pacific

    assert pacific("2026-10-16T20:09:59.000Z") == "13:09:59"  # daylight time is UTC-7
    assert pacific("2026-12-16T20:09:59.000Z") == "12:09:59"  # standard time is UTC-8
    assert pacific(None) == "-"
    assert evenly_spaced(list(range(40)), 6) == [0, 8, 16, 23, 31, 39]
    assert evenly_spaced([3, 5], 6) == [3, 5]
    # 0.0001 degree of longitude at latitude 47.6 is about 25 feet
    assert 24 < feet_between(47.6, -122.3, 47.6, -122.2999) < 26


def test_the_status_file_carries_when_it_was_checked_and_what_each_probe_found() -> None:
    from transit_reliability.monitoring.watchdog import Finding, status_payload

    payload = status_payload([Finding("a", True, "fine"), Finding("b", False, "bad")], NOW)
    assert payload["checked_at"] == NOW.isoformat()
    assert payload["findings"] == [
        {"name": "a", "ok": True, "detail": "fine"},
        {"name": "b", "ok": False, "detail": "bad"},
    ]


def test_details_a_visitor_reads_use_pacific_time() -> None:
    from transit_reliability.monitoring.watchdog import pacific

    assert pacific(dt.datetime(2026, 10, 20, 12, 30, tzinfo=dt.UTC)) == "05:30 PDT"
    assert pacific(dt.datetime(2026, 12, 20, 12, 30, tzinfo=dt.UTC)) == "04:30 PST"
    finding = job_finding("ingest", [run(10)], NOW, 75)  # NOW is 23:10 UTC, 16:10 PDT
    assert "UTC" not in finding.detail and "PDT" in finding.detail
    late = dt.datetime(2026, 10, 19, 14, 0, tzinfo=dt.UTC)
    missing = silver_finding([run(22 * 60, base=late)], late)
    assert "UTC" not in missing.detail and "04:45 PDT" in missing.detail


def test_the_rider_rule_checks_exist_and_use_the_given_day() -> None:
    from transit_reliability.audit.catalog import all_checks
    from transit_reliability.monitoring.daily import drift_checks

    names = {c.name for c in all_checks("transit", "2026-10-16")}
    assert {
        "gold_rider_counts_equal_recount_from_stop_visits",
        "route_rollup_rider_counts_conserve",
    } <= names
    drift = {c.name: c for c in drift_checks("transit", "2026-10-16")}
    check = drift["rider_rule_scored_share_of_non_last_timepoints"]
    assert "2026-10-16" in check.sql and check.minimum == 0.8


def test_weather_checks_read_the_landing_files_not_the_daily_silver_table() -> None:
    import datetime as dt

    from transit_reliability.monitoring.hourly import hourly_checks

    checks = {
        c.name: c
        for c in hourly_checks("transit", dt.datetime(2026, 10, 21, 22, 12, tzinfo=dt.UTC))
    }
    fetched = checks["weather_minutes_since_last_fetch"].sql
    assert (
        "/Volumes/workspace/transit_landing/raw/weather/dt={2026-10-20,2026-10-21}/*.json"
        in fetched
    )
    assert "weather_observations" not in fetched
    assert "weather_observations" not in checks["weather_stations_fetched_last_3_hours"].sql
    # the daily table gets a daily-sized limit, not an hourly one
    assert checks["weather_silver_hours_behind"].maximum == 30.0


def test_relay_findings() -> None:
    import datetime as dt

    from transit_reliability.monitoring.watchdog import (
        relay_memory_finding,
        relay_traffic_finding,
        relay_vehicles_finding,
    )

    now = dt.datetime(2026, 10, 22, 19, 0, tzinfo=dt.UTC)
    assert relay_vehicles_finding(200, 52_000).ok
    assert not relay_vehicles_finding(200, 120).ok  # an error page, not the bus file
    assert not relay_vehicles_finding(502, 0).ok
    assert relay_memory_finding({"lastRun": now.timestamp() - 40, "running": 600}, now).ok
    assert not relay_memory_finding({"lastRun": now.timestamp() - 600}, now).ok  # the alarm stopped
    assert not relay_memory_finding(None, now).ok
    assert relay_traffic_finding(200, {"frc": "FRC5"}).ok
    assert relay_traffic_finding(200, {"none": True}).ok
    assert not relay_traffic_finding(503, None).ok  # key not set


def test_a_run_that_ended_in_an_internal_error_counts_as_finished_and_failed() -> None:
    import datetime as dt

    from transit_reliability.monitoring.watchdog import job_finding

    now = dt.datetime(2026, 10, 22, 19, 0, tzinfo=dt.UTC)
    run = {
        "start_time": int((now - dt.timedelta(hours=7)).timestamp() * 1000),
        "state": {"life_cycle_state": "INTERNAL_ERROR", "result_state": "FAILED"},
    }
    f = job_finding("silver_job_daily_run", [run], now, max_age_min=24 * 60.0)
    assert not f.ok and "FAILED" in f.detail and "no finished run" not in f.detail
