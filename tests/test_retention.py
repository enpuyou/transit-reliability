import datetime as dt

from transit_reliability.retention import RawFile, file_date, plan_retention

BASE = "dbfs:/Volumes/workspace/transit_landing/raw/trip_updates"
TODAY = dt.date(2026, 11, 20)


def _file(day: str, name: str = "1791927780000.pb") -> RawFile:
    return RawFile(f"{BASE}/dt={day}/{name}", 100)


def test_file_date_reads_the_dt_folder() -> None:
    assert file_date(_file("2026-10-13").path) == dt.date(2026, 10, 13)
    assert file_date("dbfs:/no/date/here.pb") is None


def test_recent_files_are_kept_even_when_verified() -> None:
    recent = _file("2026-11-10")
    plan = plan_retention([recent], {recent.path}, TODAY, 30, dt.date(2026, 11, 20))
    assert plan.keep_recent == [recent] and not plan.deletable


def test_old_file_missing_from_bronze_is_blocked() -> None:
    old = _file("2026-10-13")
    plan = plan_retention([old], set(), TODAY, 30, dt.date(2026, 11, 20))
    assert plan.blocked_not_in_bronze == [old] and not plan.deletable


def test_old_ingested_file_waits_until_silver_is_verified() -> None:
    old = _file("2026-10-13")
    no_silver = plan_retention([old], {old.path}, TODAY, 30, None)
    behind = plan_retention([old], {old.path}, TODAY, 30, dt.date(2026, 10, 12))
    assert no_silver.waiting_for_silver == [old] and behind.waiting_for_silver == [old]
    assert not no_silver.deletable and not behind.deletable


def test_old_ingested_and_silver_verified_file_is_deletable() -> None:
    old = _file("2026-10-13")
    plan = plan_retention([old], {old.path}, TODAY, 30, dt.date(2026, 10, 13))
    assert plan.deletable == [old]


def test_smoke_files_are_never_touched() -> None:
    smoke = _file("2026-10-13", "tripupdates-smoke.pb")
    plan = plan_retention([smoke], {smoke.path}, TODAY, 30, dt.date(2026, 11, 20))
    assert plan.ignored == [smoke] and not plan.deletable
