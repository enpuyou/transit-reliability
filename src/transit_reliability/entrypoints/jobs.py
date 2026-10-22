"""Command-line entrypoints used by Databricks bundle jobs."""

import argparse
import datetime as dt
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
    parser.add_argument(
        "--since", help="re-read bronze rows ingested after this timestamp (backfill)"
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver_realtime import build_vehicle_positions

    build_vehicle_positions(_spark_session(), args.schema_prefix, since=args.since)


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
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.silver_stop_visits import build_stop_visits

    build_stop_visits(_spark_session(), args.schema_prefix, args.rebuild_from)


def build_gold_trip_performance() -> None:
    """Summarize each trip's punctuality from silver stop visits."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_trip_performance import build_trip_performance

    build_trip_performance(_spark_session(), args.schema_prefix, args.rebuild_from)


def build_gold_prediction_errors() -> None:
    """Score the agency's arrival predictions against inferred arrivals, with baselines."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_prediction_errors import build_prediction_errors

    build_prediction_errors(_spark_session(), args.schema_prefix, args.rebuild_from)


def build_gold_route_performance() -> None:
    """Roll trip performance up to route, direction, and start hour."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_rollups import build_route_performance

    build_route_performance(_spark_session(), args.schema_prefix, args.rebuild_from)


def build_gold_prediction_accuracy() -> None:
    """Summarize prediction errors by route, hour, and horizon."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_rollups import build_prediction_accuracy

    build_prediction_accuracy(_spark_session(), args.schema_prefix, args.rebuild_from)


def run_health_checks() -> None:
    """Fail the task when the output looks wrong (stale silver, collapsed coverage, ...)."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.ops_health import run_health_checks as run

    run(_spark_session(), args.schema_prefix)


def run_monitor() -> None:
    """Run a check suite, keep its results in <prefix>_ops.check_results, fail on real failures."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--suite", required=True, choices=["hourly", "daily"])
    parser.add_argument("--day", help="service date (daily suite); default is the newest complete")
    args = parser.parse_args()

    import datetime as dt

    from transit_reliability.monitoring.run import run_monitor as run

    day = dt.date.fromisoformat(args.day) if args.day else None
    run(_spark_session(), args.schema_prefix, args.suite, day)


def publish_site() -> None:
    """Build the <prefix>_public aggregate tables and export site.json for the website."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--state-volume", required=True)
    parser.add_argument("--parts", choices=["live", "all"], default="all")
    parser.add_argument(
        "--every-hours",
        type=int,
        default=1,
        help="only refresh in UTC hours divisible by this (other hours exit at once)",
    )
    args = parser.parse_args()

    import datetime as dt

    from transit_reliability.public.build import is_publish_hour
    from transit_reliability.public.build import publish_site as run

    if not is_publish_hour(dt.datetime.now(dt.UTC), args.every_hours):
        print(f"skipping: not a multiple-of-{args.every_hours} UTC hour")
        return
    run(_spark_session(), args.schema_prefix, args.state_volume, args.parts)


def export_map_history() -> None:
    """Write public/stop_history.json (typical lateness per timetable stop) for the live map."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--state-volume", required=True)
    args = parser.parse_args()

    from transit_reliability.public.map_data import export_map_history as run

    run(_spark_session(), args.schema_prefix, args.state_volume)


def fail_job() -> None:
    """Fail on purpose. Used by a task that runs only when an upstream task failed.

    The monitor job publishes the site even when a check fails (the page must show the failure),
    which would otherwise end the run as SUCCESS_WITH_FAILURES and send no failure email.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", default="an upstream task failed")
    args = parser.parse_args()
    raise RuntimeError(
        f"{args.reason}; see workspace.transit_ops.check_results and the failed task"
    )


def build_gold_segment_runs() -> None:
    """One row per trip and pair of consecutive timepoints: scheduled vs observed running time."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_segments import build_segment_runs

    build_segment_runs(_spark_session(), args.schema_prefix, args.rebuild_from)


def build_gold_segment_runtimes() -> None:
    """Summarise the last 28 complete days of segment runs by segment, band and day type."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_segments import build_segment_runtimes

    build_segment_runtimes(_spark_session(), args.schema_prefix)


def build_gold_headway_pairs() -> None:
    """Pair each trip with the one scheduled before it at a timepoint: scheduled vs actual gap."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument(
        "--rebuild-from",
        type=dt.date.fromisoformat,
        help="recompute service dates from this day (backfill)",
    )
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_headways import build_headway_pairs

    build_headway_pairs(_spark_session(), args.schema_prefix, args.rebuild_from)


def build_gold_headway_summary() -> None:
    """Count bunched, gapped, regular and uncertain pairs per route, band and day type (28 days)."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_headways import build_headway_summary

    build_headway_summary(_spark_session(), args.schema_prefix)


def build_silver_weather() -> None:
    """Parse landed weather files into observations and an hourly summary."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--landing-volume", required=True)
    args = parser.parse_args()

    from transit_reliability.weather.silver_weather import build_weather

    build_weather(_spark_session(), args.schema_prefix, args.landing_volume)


def build_gold_trip_delivery() -> None:
    """One row per scheduled trip and date: did it run, and was a missing one announced?"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--rebuild-from", help="recompute service dates from this one (YYYY-MM-DD)")
    args = parser.parse_args()

    import datetime as dt

    from transit_reliability.gtfs_static.gold_delivery import build_trip_delivery

    since = dt.date.fromisoformat(args.rebuild_from) if args.rebuild_from else None
    build_trip_delivery(_spark_session(), args.schema_prefix, since)


def build_gold_trip_delivery_summary() -> None:
    """Trip delivery over the last 28 complete days, by route, time band and day type."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.gtfs_static.gold_delivery import build_trip_delivery_summary

    build_trip_delivery_summary(_spark_session(), args.schema_prefix)


def build_silver_events() -> None:
    """The newest version of every event landed by the daily Ticketmaster pull."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--landing-volume", required=True)
    args = parser.parse_args()

    from transit_reliability.events.silver_events import build_events

    build_events(_spark_session(), args.schema_prefix, args.landing_volume)


def build_gold_event_routes() -> None:
    """Which bus routes pass near each event."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.events.gold_events import build_event_routes

    build_event_routes(_spark_session(), args.schema_prefix)


def build_gold_weather_impact() -> None:
    """Compare reliability across dry, foggy and rainy hours."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-prefix", required=True)
    args = parser.parse_args()

    from transit_reliability.weather.gold_weather import build_weather_impact

    build_weather_impact(_spark_session(), args.schema_prefix)
