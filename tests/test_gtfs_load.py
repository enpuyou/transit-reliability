import datetime as dt
import io
import zipfile

from transit_reliability.gtfs_static.load import (
    FeedWindow,
    choose_feed,
    dates_between,
    day_origin_epoch,
    gtfs_seconds,
    read_feed_info,
    read_rows,
)
from transit_reliability.gtfs_static.schedule import scheduled_timestamp


def _zip(files: dict[str, str]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return zipfile.ZipFile(buffer)


def test_gtfs_seconds_handles_after_midnight_and_blank() -> None:
    assert gtfs_seconds("08:10:00") == 8 * 3600 + 600
    assert gtfs_seconds("25:10:00") == 25 * 3600 + 600
    assert gtfs_seconds(" ") is None and gtfs_seconds("") is None


def test_origin_plus_seconds_equals_scheduled_timestamp_on_every_kind_of_day() -> None:
    for day in (dt.date(2026, 10, 13), dt.date(2026, 11, 1), dt.date(2027, 3, 14)):
        for text in ("00:00:00", "08:10:00", "25:10:00"):
            seconds = gtfs_seconds(text)
            assert seconds is not None
            expected = int(scheduled_timestamp(day, text).timestamp())
            assert day_origin_epoch(day) + seconds == expected


def test_read_rows_types_columns_and_derives_seconds() -> None:
    archive = _zip(
        {
            "stop_times.txt": (
                "﻿trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,"
                "timepoint,shape_dist_traveled\n"
                "t1,24:30:00,24:31:00,s1,3,0,1,12.5\n"
                "t1,,,s2,4,0,,\n"
            )
        }
    )
    first, second = list(read_rows(archive, "stop_times"))
    assert first == (
        "t1",
        "24:30:00",
        "24:31:00",
        "s1",
        3,
        1,
        12.5,
        24 * 3600 + 1800,
        24 * 3600 + 1860,
    )
    assert second == ("t1", None, None, "s2", 4, None, None, None, None)


def test_read_feed_info() -> None:
    archive = _zip(
        {"feed_info.txt": "feed_version,feed_start_date,feed_end_date,x\nV1,20260928,20270326,y\n"}
    )
    assert read_feed_info(archive) == {
        "feed_version": "V1",
        "feed_start_date": "20260928",
        "feed_end_date": "20270326",
    }


def test_choose_feed_prefers_latest_download_covering_the_date() -> None:
    old = FeedWindow("OLD", dt.date(2026, 9, 28), dt.date(2026, 9, 28), dt.date(2026, 12, 31))
    new = FeedWindow("NEW", dt.date(2026, 12, 1), dt.date(2026, 12, 7), dt.date(2027, 3, 26))
    assert choose_feed(dt.date(2026, 10, 14), [old, new]) == old
    assert choose_feed(dt.date(2026, 12, 20), [old, new]) == new  # covered by both: newest wins
    assert choose_feed(dt.date(2027, 6, 1), [old, new]) is None


def test_dates_between_is_inclusive() -> None:
    assert dates_between(dt.date(2026, 10, 13), dt.date(2026, 10, 15)) == [
        dt.date(2026, 10, 13),
        dt.date(2026, 10, 14),
        dt.date(2026, 10, 15),
    ]
