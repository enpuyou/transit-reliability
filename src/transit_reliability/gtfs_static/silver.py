"""Spark glue for static GTFS: load feed zips into silver and expand to service dates.

Everything testable lives in `load.py` and `schedule.py`; this module only moves data.
Import Spark and pandas inside functions so the package imports locally.
"""

import datetime as dt
import os
import zipfile
from typing import Any

from transit_reliability.bronze.schema import volume_path
from transit_reliability.gtfs_static.load import (
    TABLES,
    FeedWindow,
    choose_feed,
    dates_between,
    day_origin_epoch,
    read_feed_info,
    read_rows,
)
from transit_reliability.gtfs_static.schedule import active_service_ids

FEEDS_TABLE = "gtfs_feeds"
SCHEDULED_TABLE = "scheduled_stop_times"


def _compact_date(text: str) -> dt.date:
    """Parse a GTFS yyyymmdd date."""

    return dt.datetime.strptime(text, "%Y%m%d").date()


def _silver(prefix: str, table: str) -> str:
    return f"workspace.{prefix}_silver.{table}"


def _spark_type(kind: str) -> Any:
    from pyspark.sql.types import DoubleType, IntegerType, StringType

    return {"str": StringType(), "int": IntegerType(), "float": DoubleType()}[kind]


def _replace_feed_rows(spark: Any, table: str, feed_version: str) -> None:
    if spark.catalog.tableExists(table):
        spark.sql(f"DELETE FROM {table} WHERE feed_version = '{feed_version}'")


def _load_table(spark: Any, archive: zipfile.ZipFile, stem: str, prefix: str, feed: str) -> int:
    import pandas as pd
    from pyspark.sql import functions as F
    from pyspark.sql.types import IntegerType, StructField, StructType

    spec = TABLES[stem]
    kinds = dict(spec.columns)
    schema = StructType(
        [StructField(name, _spark_type(kinds[name]), True) for name, _ in spec.columns]
        + [StructField(name, IntegerType(), True) for name, _ in spec.seconds]
    )
    frame = pd.DataFrame.from_records(read_rows(archive, stem), columns=list(spec.names))
    frame = frame.astype(object).where(frame.notna(), None)  # NaN -> NULL
    df = spark.createDataFrame(frame, schema).withColumn("feed_version", F.lit(feed))
    table = _silver(prefix, spec.table)
    _replace_feed_rows(spark, table, feed)
    df.write.format("delta").mode("append").saveAsTable(table)
    return int(df.count())


def load_feeds(spark: Any, landing_volume: str, prefix: str) -> None:
    """Load every landed GTFS zip whose feed_version is not registered yet."""

    from pyspark.sql import functions as F

    root = volume_path(landing_volume, "gtfs_static")
    known = (
        {r["feed_version"] for r in spark.table(_silver(prefix, FEEDS_TABLE)).collect()}
        if spark.catalog.tableExists(_silver(prefix, FEEDS_TABLE))
        else set()
    )
    for folder in sorted(d for d in os.listdir(root) if d.startswith("dt=")):
        path = os.path.join(root, folder, "google_transit.zip")
        if not os.path.exists(path):
            continue
        with zipfile.ZipFile(path) as archive:
            info = read_feed_info(archive)
            version = info["feed_version"]
            if version in known:
                print(f"skip {folder}: feed {version} already loaded")
                continue
            for stem in TABLES:
                rows = _load_table(spark, archive, stem, prefix, version)
                print(f"loaded {stem}: {rows} rows for feed {version}")
        row = [
            (
                version,
                folder.removeprefix("dt="),
                info["feed_start_date"],
                info["feed_end_date"],
                path,
            )
        ]
        registry = spark.createDataFrame(
            row,
            "feed_version string, feed_date string, feed_start string, feed_end string, "
            "source_file string",
        ).withColumn("loaded_at", F.current_timestamp())
        registry.write.format("delta").mode("append").saveAsTable(_silver(prefix, FEEDS_TABLE))
        known.add(version)


def _feed_windows(spark: Any, prefix: str) -> list[FeedWindow]:
    parse = dt.date.fromisoformat
    return [
        FeedWindow(
            r["feed_version"],
            parse(r["feed_date"]),
            _compact_date(r["feed_start"]),
            _compact_date(r["feed_end"]),
        )
        for r in spark.table(_silver(prefix, FEEDS_TABLE)).collect()
    ]


def _expand_one_day(spark: Any, prefix: str, day: dt.date, feed: FeedWindow) -> int:
    from pyspark.sql import functions as F

    def rows(table: str) -> list[dict[str, str]]:
        data = spark.table(_silver(prefix, table)).where(F.col("feed_version") == feed.feed_version)
        return [
            {k: "" if v is None else str(v) for k, v in r.asDict().items()} for r in data.collect()
        ]

    active = active_service_ids(day, rows("gtfs_calendar"), rows("gtfs_calendar_dates"))
    trips = (
        spark.table(_silver(prefix, "gtfs_trips"))
        .where(F.col("feed_version") == feed.feed_version)
        .where(F.col("service_id").isin(sorted(active)))
        .select("trip_id", "route_id", "direction_id", "service_id")
    )
    stop_times = spark.table(_silver(prefix, "gtfs_stop_times")).where(
        F.col("feed_version") == feed.feed_version
    )
    origin = day_origin_epoch(day)
    out = stop_times.join(trips, "trip_id").select(
        F.lit(day.isoformat()).cast("date").alias("service_date"),
        F.lit(feed.feed_version).alias("feed_version"),
        "trip_id",
        "route_id",
        "direction_id",
        "service_id",
        "stop_sequence",
        "stop_id",
        F.timestamp_seconds(F.lit(origin) + F.col("arrival_seconds")).alias("scheduled_arrival_ts"),
        F.timestamp_seconds(F.lit(origin) + F.col("departure_seconds")).alias(
            "scheduled_departure_ts"
        ),
        "timepoint",
    )
    target = _silver(prefix, SCHEDULED_TABLE)
    if spark.catalog.tableExists(target):
        spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
    out.write.format("delta").mode("append").saveAsTable(target)
    return int(
        spark.table(target)
        .where(F.col("service_date") == F.lit(day.isoformat()).cast("date"))
        .count()
    )


def expand_schedule(spark: Any, prefix: str) -> None:
    """Expand the schedule to one row per service date, trip, and stop.

    Only service dates that have realtime data are expanded (about 450K rows per day), not the
    whole feed window (tens of millions of rows, mostly never compared). Done dates are skipped.
    """

    from pyspark.sql import functions as F

    bounds = (
        spark.table(f"workspace.{prefix}_bronze.vehicle_positions")
        .where(F.col("start_date").rlike("^[0-9]{8}$"))
        .agg(F.min("start_date").alias("first"), F.max("start_date").alias("last"))
        .collect()[0]
    )
    if bounds["first"] is None:
        print("no realtime service dates yet")
        return
    wanted = dates_between(_compact_date(bounds["first"]), _compact_date(bounds["last"]))
    target = _silver(prefix, SCHEDULED_TABLE)
    done = (
        {r["service_date"] for r in spark.table(target).select("service_date").distinct().collect()}
        if spark.catalog.tableExists(target)
        else set()
    )
    feeds = _feed_windows(spark, prefix)
    for day in (d for d in wanted if d not in done):
        feed = choose_feed(day, feeds)
        if feed is None:
            print(f"no feed covers {day}; skipped")
            continue
        count = _expand_one_day(spark, prefix, day, feed)
        print(f"expanded {day} with feed {feed.feed_version}: {count} rows")
