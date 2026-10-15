"""Pure schedule logic for static GTFS: service days and scheduled timestamps."""

import datetime as dt
from collections.abc import Iterable, Mapping
from zoneinfo import ZoneInfo

AGENCY_TZ = ZoneInfo("America/Los_Angeles")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
EXCEPTION_ADDED = "1"
EXCEPTION_REMOVED = "2"


def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y%m%d").date()


def scheduled_timestamp(
    service_date: dt.date, gtfs_time: str, tz: ZoneInfo = AGENCY_TZ
) -> dt.datetime:
    """Convert a GTFS time (may exceed 24:00:00) on a service date to an aware UTC datetime.

    GTFS measures times from "noon minus 12 hours" of the service date, not from midnight.
    On an ordinary day that is midnight. On a daylight-saving day it differs by an hour, and
    that is what keeps scheduled times on the local wall clock. The subtraction must happen
    in UTC: aware-datetime arithmetic in a zone is wall-clock arithmetic and would be wrong.
    """

    hours, minutes, seconds = (int(part) for part in gtfs_time.split(":"))
    noon_utc = dt.datetime(
        service_date.year, service_date.month, service_date.day, 12, tzinfo=tz
    ).astimezone(dt.UTC)
    offset = dt.timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return noon_utc - dt.timedelta(hours=12) + offset


def active_service_ids(
    service_date: dt.date,
    calendar: Iterable[Mapping[str, str]],
    calendar_dates: Iterable[Mapping[str, str]],
) -> set[str]:
    """Service ids running on a date: weekly pattern inside its range, then exceptions."""

    weekday = WEEKDAYS[service_date.weekday()]
    active = {
        row["service_id"]
        for row in calendar
        if row[weekday] == "1"
        and _parse_date(row["start_date"]) <= service_date <= _parse_date(row["end_date"])
    }
    for row in calendar_dates:
        if _parse_date(row["date"]) != service_date:
            continue
        if row["exception_type"] == EXCEPTION_ADDED:
            active.add(row["service_id"])
        elif row["exception_type"] == EXCEPTION_REMOVED:
            active.discard(row["service_id"])
    return active
