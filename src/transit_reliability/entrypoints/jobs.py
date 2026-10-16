"""Command-line entrypoints used by Databricks bundle jobs."""

import argparse
from typing import Any


def _spark_session() -> Any:
    from pyspark.sql import SparkSession

    return SparkSession.builder.getOrCreate()


def setup_schemas() -> None:
    """Create target schemas and the state volume if they do not exist."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--state-volume", required=True)
    args = parser.parse_args()
    spark = _spark_session()
    for suffix in ("bronze", "silver", "gold", "ml", "ops"):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS workspace.{args.schema_prefix}_{suffix}")
    catalog, schema, volume = args.state_volume.split(".", maxsplit=2)
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {args.state_volume}")


def ingest_bronze() -> None:
    """Run all three GTFS-Realtime bronze streams once with availableNow."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--landing-volume", required=True)
    parser.add_argument("--state-volume", required=True)
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.bronze.streaming import run_source

    spark = _spark_session()
    for source in ("vehicle_positions", "trip_updates", "alerts"):
        run_source(
            spark,
            source,
            args.landing_volume,
            args.state_volume,
            args.schema_prefix,
        )


def load_static_gtfs() -> None:
    """Load newly landed static GTFS zips into versioned silver tables."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--landing-volume", required=True)
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver import load_feeds

    load_feeds(_spark_session(), args.landing_volume, args.schema_prefix)


def expand_schedule() -> None:
    """Expand the schedule to scheduled stop times for each service date with realtime data."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver import expand_schedule as run

    run(_spark_session(), args.schema_prefix)


def build_silver_vehicle_positions() -> None:
    """Dedupe bronze vehicle positions into silver, quarantine bad rows, flag schedule matches."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver_realtime import build_vehicle_positions

    build_vehicle_positions(_spark_session(), args.schema_prefix)


def expand_trips() -> None:
    """Expand the schedule to one row per trip per service date, through tomorrow."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver import expand_trips as run

    run(_spark_session(), args.schema_prefix)


def build_silver_trip_updates() -> None:
    """Collapse bronze trip-update snapshots into one row per unchanged prediction run."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--until-ts", type=int, help="stop at this epoch (tests, staged backfill)")
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver_trip_updates import build_trip_update_snapshots

    build_trip_update_snapshots(_spark_session(), args.schema_prefix, until_ts=args.until_ts)


def build_silver_stop_visits() -> None:
    """Infer when each bus reached and left each scheduled stop, per service date."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver_stop_visits import build_stop_visits

    build_stop_visits(_spark_session(), args.schema_prefix)


def build_gold_trip_performance() -> None:
    """Summarize each trip's punctuality from silver stop visits."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_trip_performance import build_trip_performance

    build_trip_performance(_spark_session(), args.schema_prefix)
