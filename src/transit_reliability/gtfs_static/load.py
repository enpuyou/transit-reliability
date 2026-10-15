"""Static GTFS loading: typed row readers and pure helpers; Spark glue is in `silver.py`."""

import csv
import datetime as dt
import io
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from transit_reliability.gtfs_static.schedule import scheduled_timestamp


@dataclass(frozen=True)
class TableSpec:
    """A GTFS file to load: kept columns as (name, kind), kind in str/int/float."""

    table: str
    columns: tuple[tuple[str, str], ...]
    # Extra integer columns computed from GTFS time strings: (new name, source column).
    seconds: tuple[tuple[str, str], ...] = field(default=())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.columns) + tuple(n for n, _ in self.seconds)


TABLES: dict[str, TableSpec] = {
    "routes": TableSpec(
        "gtfs_routes",
        (
            ("route_id", "str"),
            ("agency_id", "str"),
            ("route_short_name", "str"),
            ("route_long_name", "str"),
            ("route_type", "int"),
            ("route_color", "str"),
        ),
    ),
    "trips": TableSpec(
        "gtfs_trips",
        (
            ("route_id", "str"),
            ("service_id", "str"),
            ("trip_id", "str"),
            ("trip_headsign", "str"),
            ("direction_id", "int"),
            ("block_id", "str"),
            ("shape_id", "str"),
        ),
    ),
    "stops": TableSpec(
        "gtfs_stops",
        (
            ("stop_id", "str"),
            ("stop_code", "str"),
            ("stop_name", "str"),
            ("stop_lat", "float"),
            ("stop_lon", "float"),
            ("location_type", "int"),
            ("parent_station", "str"),
        ),
    ),
    "stop_times": TableSpec(
        "gtfs_stop_times",
        (
            ("trip_id", "str"),
            ("arrival_time", "str"),
            ("departure_time", "str"),
            ("stop_id", "str"),
            ("stop_sequence", "int"),
            ("timepoint", "int"),
            ("shape_dist_traveled", "float"),
        ),
        seconds=(("arrival_seconds", "arrival_time"), ("departure_seconds", "departure_time")),
    ),
    "shapes": TableSpec(
        "gtfs_shapes",
        (
            ("shape_id", "str"),
            ("shape_pt_lat", "float"),
            ("shape_pt_lon", "float"),
            ("shape_pt_sequence", "int"),
            ("shape_dist_traveled", "float"),
        ),
    ),
    "calendar": TableSpec(
        "gtfs_calendar",
        (
            ("service_id", "str"),
            ("monday", "int"),
            ("tuesday", "int"),
            ("wednesday", "int"),
            ("thursday", "int"),
            ("friday", "int"),
            ("saturday", "int"),
            ("sunday", "int"),
            ("start_date", "str"),
            ("end_date", "str"),
        ),
    ),
    "calendar_dates": TableSpec(
        "gtfs_calendar_dates",
        (("service_id", "str"), ("date", "str"), ("exception_type", "int")),
    ),
}


def gtfs_seconds(value: str) -> int | None:
    """Seconds since the GTFS day origin for 'H:MM:SS' (hours may reach 24 or more)."""

    text = value.strip()
    if not text:
        return None
    hours, minutes, seconds = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _cast(value: str, kind: str) -> Any:
    text = value.strip()
    if kind == "str":
        return text or None
    if not text:
        return None
    return int(text) if kind == "int" else float(text)


def read_rows(archive: zipfile.ZipFile, stem: str) -> Iterator[tuple[Any, ...]]:
    """Yield typed tuples for one GTFS file, in `TableSpec.names` order."""

    spec = TABLES[stem]
    with archive.open(f"{stem}.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        for row in reader:
            values = [_cast(row.get(name) or "", kind) for name, kind in spec.columns]
            for _, source in spec.seconds:
                values.append(gtfs_seconds(row.get(source) or ""))
            yield tuple(values)


def read_feed_info(archive: zipfile.ZipFile) -> dict[str, str]:
    """feed_version, feed_start_date, feed_end_date from feed_info.txt."""

    with archive.open("feed_info.txt") as raw:
        row = next(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))
    return {key: row[key] for key in ("feed_version", "feed_start_date", "feed_end_date")}


@dataclass(frozen=True)
class FeedWindow:
    feed_version: str
    feed_date: dt.date  # the landing-folder date the zip was downloaded
    start: dt.date
    end: dt.date


def choose_feed(service_date: dt.date, feeds: Sequence[FeedWindow]) -> FeedWindow | None:
    """The feed to use for a service date: valid that day, latest download wins."""

    valid = [f for f in feeds if f.start <= service_date <= f.end]
    return max(valid, key=lambda f: f.feed_date) if valid else None


def day_origin_epoch(service_date: dt.date) -> int:
    """Epoch seconds of GTFS time 00:00:00 on a service date (DST-safe)."""

    return int(scheduled_timestamp(service_date, "00:00:00").timestamp())


def dates_between(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=n) for n in range((last - first).days + 1)]


def trip_expansion_dates(
    first_realtime: dt.date, last_realtime: dt.date, today: dt.date
) -> list[dt.date]:
    """Service dates to expand into scheduled trips: every realtime date, through tomorrow.

    Running one day ahead means a new service date (which starts at local midnight) already has
    its trips when its first realtime rows arrive, so `matched_trip` is never unknown for it.
    """

    return dates_between(first_realtime, max(last_realtime, today + dt.timedelta(days=1)))
