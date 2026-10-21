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
        "late and uncertain timepoints. The headline is the rider rule: early is judged on when "
        "the bus leaves, late on when it arrives, the last stop is left out. Arrival-only and "
        "departure-only versions are kept beside it.",
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
        "gold",
        "segment_runs",
        "How long each trip really took between two consecutive timepoints, next to the scheduled "
        "time, with how uncertain our estimate is and whether a detour alert covered the route.",
        "one trip between two consecutive timepoints on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "gold",
        "segment_runtimes",
        "The last 28 complete days of those runs summarised by route segment, time of day and day "
        "type: typical, 85th-percentile and 95th-percentile running time against the timetable.",
        "one route segment in one time band on one day type, over the last 28 days",
        "daily silver job",
        "max(last_day)",
    ),
    TableInfo(
        "gold",
        "headway_pairs",
        "How far apart consecutive buses of a route really were at a timepoint, next to the "
        "scheduled gap, and whether the pair was bunched, gapped, regular or too uncertain.",
        "one trip and the trip scheduled just before it at one timepoint on one service date",
        "daily silver job",
        "max(service_date)",
    ),
    TableInfo(
        "gold",
        "headway_summary",
        "Counts of bunched, gapped, regular and uncertain pairs per route, direction, time of day "
        "and day type over the last 28 days, for frequent service only.",
        "one route direction in one time band on one day type, over the last 28 days",
        "daily silver job",
        "max(built_through)",
    ),
    TableInfo(
        "silver",
        "weather_observations",
        "Readings from three Seattle-area weather stations about every 5 minutes: temperature, "
        "wind, visibility, humidity, and what the station reports (rain, fog, clear).",
        "one reading from one weather station at one moment",
        "daily silver job (files fetched hourly from the National Weather Service)",
        "max(observed_ts)",
    ),
    TableInfo(
        "silver",
        "weather_hourly",
        "Those readings summarised per hour across the stations, with the hour labelled rain, fog "
        "or dry (rain when over a quarter of readings that hour report precipitation).",
        "one hour of weather in Seattle",
        "daily silver job",
        "max(hour_start)",
    ),
    TableInfo(
        "gold",
        "weather_impact",
        "How reliability compares across dry, foggy and rainy hours, within the same time of day: "
        "trips are matched to the weather in the hour they started.",
        "one weather class in one time band on one day type",
        "daily silver job",
        "current_timestamp()",
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
    "On time (rider rule)": "The headline. At each timepoint except the last stop: 'early' if "
    "the bus left more than 1 minute early, 'late' if it arrived more than 5 minutes late (at "
    "the first stop, where the bus is parked, if it left more than 5 minutes late), otherwise "
    "'on time' when we are sure it was neither. Judged on the whole interval: if the interval "
    "straddles a limit the timepoint is counted 'uncertain', not guessed. A bus that arrives "
    "early and waits is normal and is not counted as early.",
    "Arrival-only view": "The older measure: the same window (1 minute early to 5 minutes late) "
    "applied to the arrival time at every timepoint with a measured arrival. Shown beside the "
    "headline for comparison.",
    "Medallion layers": "Bronze (raw, decoded), silver (cleaned and matched), gold (answers).",
    "Delta table": "A table format on Databricks that keeps a history of every change.",
    "Running time": "How long a bus takes between two consecutive timepoints. The timetable "
    "says how long it should take; we estimate how long it did take from GPS, so each figure "
    "has an uncertainty of about a minute that shrinks when many trips are averaged.",
    "Bunching": "Buses on a frequent route are supposed to arrive evenly spaced. Bunching is two "
    "buses arriving almost together (here: less than a quarter of the scheduled gap apart), which "
    "leaves a long wait for the next rider; a gap is the opposite (more than twice the scheduled "
    "gap). Spacing is estimated from GPS, so a pair is only called bunched or a gap when the whole "
    "estimated range says so.",
    "Weather class": "Each hour is labelled rain, fog or dry from what three Seattle-area weather "
    "stations report. The service does not give rain amounts, only whether it is raining and how "
    "hard (light, moderate, heavy). Rain is called when over a quarter of the hour's readings "
    "report precipitation.",
    "DBU": "Databricks Unit: the measure of compute used. The free plan has an undisclosed daily "
    "limit.",
}
