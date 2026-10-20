"""What each stage and table is, in plain language, for people who know nothing about the pipeline.

This text is data: it is written into the site file next to live row counts, so the website never
has to guess what a table is. Keep each sentence true; `grain` says what ONE ROW is.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TableInfo:
    layer: str
    name: str  # table name inside the layer's schema
    purpose: str
    grain: str  # what one row is
    built_by: str
    # SQL (without the prefix) for "newest data time" and whether to count rows live
    newest_expr: str | None = None
    sample: bool = True


@dataclass(frozen=True)
class LayerInfo:
    id: str
    title: str
    what: str
    why: str


LAYERS = (
    LayerInfo(
        "collector",
        "1. Collector (Cloudflare Worker)",
        "A tiny program that runs every minute on Cloudflare. It downloads King County Metro's "
        "public real-time feeds (bus positions about every 20 seconds, predicted arrival times, "
        "service alerts) and uploads the untouched bytes.",
        "Databricks Free Edition blocks most outbound internet, so the data is pushed in from "
        "outside. The collector never filters or fixes anything: these feeds cannot be re-fetched "
        "later, so the original is kept exactly as received.",
    ),
    LayerInfo(
        "landing",
        "2. Landing (raw files)",
        "A folder of one small file per download, named by the moment it was taken. Nothing "
        "interprets these files yet.",
        "It is the permanent record. If any later step has a bug, everything downstream can be "
        "rebuilt from these files.",
    ),
    LayerInfo(
        "bronze",
        "3. Bronze (parsed, nothing removed)",
        "The raw files decoded into tables, one row per item in each download. Every download is "
        "kept, including repeats of the same information.",
        "Decoding is mechanical and safe; deciding what is duplicate or wrong is a judgement, so "
        "it waits until the next layer where it can be redone.",
    ),
    LayerInfo(
        "silver",
        "4. Silver (cleaned, matched to the schedule, arrivals inferred)",
        "Repeats collapsed, impossible rows set aside with a reason, every bus matched to its "
        "scheduled trip, and, for every scheduled stop, an estimate of when the bus arrived and "
        "left, with an interval saying how sure we are.",
        "This is where raw measurements become facts about what buses did. The arrival times are "
        "estimates from GPS positions along the route, never presented as exact.",
    ),
    LayerInfo(
        "gold",
        "5. Gold (the numbers people read)",
        "One row per trip, route-hour and prediction, summarising how late or early buses were and "
        "how accurate the agency's own arrival predictions were.",
        "These are the answers: how reliable is each route, and can riders trust the predicted "
        "times. Counts are always stored next to percentages so any percentage can be rechecked.",
    ),
    LayerInfo(
        "ops",
        "6. Monitoring (checks that prove it works)",
        "Every hour and every day a set of checks measures freshness, volume, duplicates, "
        "reconciliation between layers and plausibility, and keeps each result with the SQL that "
        "produced it.",
        "A job that finishes is not a job that worked. These checks compare the output with what a "
        "healthy pipeline must look like.",
    ),
)

TABLES = (
    TableInfo(
        "bronze",
        "vehicle_positions",
        "Where each bus was, as reported by the agency.",
        "one bus in one download (the same position repeats across downloads)",
        "ingest job, every 30 minutes",
        "timestamp_seconds(max(feed_header_ts))",
    ),
    TableInfo(
        "bronze",
        "trip_updates",
        "The agency's predicted arrival and departure times for upcoming stops.",
        "one predicted stop of one trip in one download",
        "ingest job, every 30 minutes",
        "timestamp_seconds(max(feed_header_ts))",
    ),
    TableInfo(
        "bronze",
        "alerts",
        "Service alerts (detours, delays, closures) as published.",
        "one alert in one download",
        "ingest job, every 30 minutes",
        "timestamp_seconds(max(feed_header_ts))",
    ),
    TableInfo(
        "silver",
        "vehicle_positions",
        "Each distinct GPS position of each bus once, matched to its scheduled trip.",
        "one distinct GPS fix of one bus",
        "daily silver job",
        "max(vehicle_ts)",
    ),
    TableInfo(
        "silver",
        "quarantine_vehicle_positions",
        "Positions that broke a rule (no timestamp, zero coordinates, outside the region, "
        "stale), kept with the reason instead of being deleted.",
        "one rejected position, with its reason",
        "daily silver job",
        "max(bronze_ingested_at)",
    ),
    TableInfo(
        "silver",
        "trip_update_snapshots",
        "The history of each predicted stop time, collapsed so a prediction that did not change "
        "is stored once with the time range it stood.",
        "one prediction value of one stop of one trip, with first and last time seen",
        "daily silver job",
        "max(last_seen)",
    ),
    TableInfo(
        "silver",
        "scheduled_trips",
        "Which scheduled trips run on which date, expanded from the published timetable.",
        "one scheduled trip on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "silver",
        "scheduled_stop_times",
        "The timetable for each stop of each trip on each date, as real timestamps.",
        "one scheduled stop of one trip on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "silver",
        "stop_visits",
        "For every scheduled stop: when the bus reached and left it (estimated from GPS along "
        "the route) with an interval, and how late that was.",
        "one scheduled stop of one trip on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "gold",
        "trip_performance",
        "How each trip ran: typical and worst delay, how delay grew, and counts of early, on-time, "
        "late and uncertain arrivals under three definitions of on time.",
        "one trip on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "gold",
        "route_performance",
        "The trip numbers rolled up by route, direction and hour of day. Counts are added, "
        "never averaged.",
        "one route and direction in one start hour on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "gold",
        "prediction_errors",
        "Each agency prediction compared with the arrival we inferred later, at 5, 15 and 30 "
        "minutes ahead, next to two simple baselines.",
        "one scored prediction (a stop of a trip at one horizon)",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "gold",
        "prediction_accuracy",
        "Median and 90th-percentile prediction error by route and hour, for the agency and for "
        "the baselines.",
        "one route in one hour at one horizon on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "ops",
        "check_results",
        "Every monitoring check ever run: value, rule, verdict, and the SQL to rerun it.",
        "one check in one run",
        "monitor job (hourly) and the daily checks",
        "max(run_ts)",
    ),
)

GLOSSARY = {
    "GTFS": "General Transit Feed Specification: the standard format for transit timetables.",
    "GTFS-realtime": "The companion format for live data: vehicle positions, predicted arrivals, "
    "alerts.",
    "Service date": "The day a trip belongs to for scheduling. A trip that starts at 11:30 pm and "
    "ends after midnight still belongs to the earlier date.",
    "Timepoint": "A stop with a published scheduled time that buses are expected to keep to.",
    "Interval": "Our arrival times are estimates. The interval is the range from the last GPS fix "
    "before the bus reached the stop to the first one after; the true time lies inside it.",
    "On time (common lens)": "Arrived between 1 minute early and 5 minutes late. Judged on the "
    "whole interval: if the interval straddles a limit the arrival is counted 'uncertain', "
    "not guessed.",
    "Medallion layers": "Bronze (raw, decoded), silver (cleaned and matched), gold (answers).",
    "Delta table": "A table format on Databricks that keeps a history of every change.",
    "DBU": "Databricks Unit: the measure of compute used. The free plan has an undisclosed daily "
    "limit.",
}
