import datetime as dt
from zoneinfo import ZoneInfo

from transit_reliability.gtfs_static.schedule import active_service_ids, scheduled_timestamp

LA = ZoneInfo("America/Los_Angeles")
UTC = dt.UTC


def test_ordinary_day_matches_local_wall_clock() -> None:
    ts = scheduled_timestamp(dt.date(2026, 10, 13), "08:10:00")
    assert ts == dt.datetime(2026, 10, 13, 15, 10, tzinfo=UTC)
    assert ts.astimezone(LA).hour == 8


def test_time_past_24_hours_belongs_to_the_next_calendar_day() -> None:
    ts = scheduled_timestamp(dt.date(2026, 10, 13), "25:10:00")
    assert ts == dt.datetime(2026, 10, 14, 8, 10, tzinfo=UTC)
    local = ts.astimezone(LA)
    assert (local.day, local.hour, local.minute) == (14, 1, 10)


def test_fall_back_day_is_25_hours_and_midday_stays_on_the_wall_clock() -> None:
    # 2026-11-01: PDT -> PST at 02:00. GTFS time 0 is "noon minus 12h", i.e. 01:00 PDT.
    day = dt.date(2026, 11, 1)
    assert scheduled_timestamp(day, "00:00:00") == dt.datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    assert scheduled_timestamp(day, "08:00:00").astimezone(LA).hour == 8


def test_spring_forward_day_is_23_hours_and_midday_stays_on_the_wall_clock() -> None:
    # 2027-03-14: PST -> PDT at 02:00. "Noon minus 12h" is 23:00 PST the evening before.
    day = dt.date(2027, 3, 14)
    assert scheduled_timestamp(day, "00:00:00") == dt.datetime(2027, 3, 14, 7, 0, tzinfo=UTC)
    assert scheduled_timestamp(day, "08:00:00").astimezone(LA).hour == 8


CALENDAR = [
    {
        "service_id": "weekday",
        "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1", "friday": "1",
        "saturday": "0", "sunday": "0",
        "start_date": "20261001", "end_date": "20261031",
    },
    {
        "service_id": "never",
        "monday": "0", "tuesday": "0", "wednesday": "0", "thursday": "0", "friday": "0",
        "saturday": "0", "sunday": "0",
        "start_date": "20261001", "end_date": "20261031",
    },
]  # fmt: skip
EXCEPTIONS = [
    {"service_id": "weekday", "date": "20261012", "exception_type": "2"},  # holiday: removed
    {"service_id": "never", "date": "20261012", "exception_type": "1"},  # added
    {"service_id": "special", "date": "20261013", "exception_type": "1"},  # no calendar row
]


def test_regular_weekday_service_runs() -> None:
    assert active_service_ids(dt.date(2026, 10, 14), CALENDAR, EXCEPTIONS) == {"weekday"}


def test_weekend_has_no_weekday_service() -> None:
    assert active_service_ids(dt.date(2026, 10, 17), CALENDAR, EXCEPTIONS) == set()


def test_exceptions_remove_and_add_service() -> None:
    assert active_service_ids(dt.date(2026, 10, 12), CALENDAR, EXCEPTIONS) == {"never"}


def test_service_defined_only_by_calendar_dates_is_active() -> None:
    assert active_service_ids(dt.date(2026, 10, 13), CALENDAR, EXCEPTIONS) == {"weekday", "special"}


def test_service_outside_its_date_range_does_not_run() -> None:
    assert active_service_ids(dt.date(2026, 11, 2), CALENDAR, EXCEPTIONS) == set()
