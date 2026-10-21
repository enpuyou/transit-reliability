# ruff: noqa: E501  (SQL is kept on readable single lines so it can be pasted into the editor)
"""Checks that run every hour: is data flowing, on time, in the usual volume, and is the schedule current?

Each is one SELECT returning one number, so it can be pasted into the SQL editor to see the same
value. Thresholds are loose on purpose (they catch an outage or a collapse, not normal variation) and
come from measured behaviour:
- ingest runs every 30 min, so a bronze table more than 100 min old means at least two runs were missed;
- the collector lands about 180 vehicle, 60 trip-update and 12 alert snapshots per hour; the nightly
  empty-feed window (about 03:05 to 03:16 Pacific) removes about 11 minutes of vehicle snapshots;
- the longest gap seen between snapshots in normal operation is about 6 minutes (the nightly window
  is about 11), so 15 minutes is an outage.
"""

import datetime as dt

from transit_reliability.audit.core import CheckSpec
from transit_reliability.bronze.schema import volume_path

PACIFIC_DATE = "to_date(from_utc_timestamp(current_timestamp(), 'America/Los_Angeles'))"
BRONZE_SOURCES = ("vehicle_positions", "trip_updates", "alerts")
# snapshots expected per full hour, and the least we accept
MIN_SNAPSHOTS_PER_HOUR = {"vehicle_positions": 120, "trip_updates": 40, "alerts": 6}
MAX_MINUTES_SINCE_INGEST = 100.0
MAX_MINUTES_SINCE_LAST_SNAPSHOT = 90.0
MAX_SNAPSHOT_GAP_S = 900.0
MAX_HOURS_SILVER_BEHIND = 30.0  # the daily job runs at 11:45 UTC
MAX_DAYS_GOLD_BEHIND = 2
MIN_TRIPS_PER_DAY = 4000  # weekdays have about 12,900, Saturday about 9,500
MIN_DAYS_OF_SCHEDULE_LEFT = 14
LANDING_VOLUME = "workspace.transit_landing.raw"
# the weather workflow runs hourly at :20; 4 hours is three missed runs
MAX_MINUTES_SINCE_WEATHER_FETCH = 240.0
MIN_WEATHER_STATIONS_3H = 2  # of three stations
MAX_HOURS_WEATHER_SILVER_BEHIND = 30.0  # silver weather is rebuilt once a day by the silver job


def weather_files(now: dt.datetime) -> str:
    """The landed weather files of yesterday and today (UTC), as a table of path names.

    Files are named `<fetch time in epoch ms>-<station>.json`, so the name says when the fetch happened.
    Only two day folders are read to keep the check cheap.
    """

    days = ",".join(((now - dt.timedelta(days=n)).date().isoformat() for n in (1, 0)))
    folder = volume_path(LANDING_VOLUME, "weather", "dt={" + days + "}", "*.json")
    return f"read_files('{folder}', format => 'binaryFile')"


def hourly_checks(prefix: str, now: dt.datetime | None = None) -> list[CheckSpec]:
    files = weather_files(now or dt.datetime.now(dt.UTC))
    fetched_at = "CAST(regexp_extract(path, '/([0-9]+)-[A-Z0-9]+\\\\.json$', 1) AS BIGINT) / 1000.0"
    station = "regexp_extract(path, '-([A-Z0-9]+)\\\\.json$', 1)"

    def bronze(source: str) -> str:
        return f"workspace.{prefix}_bronze.{source}"

    silver = f"workspace.{prefix}_silver"
    gold = f"workspace.{prefix}_gold"
    out: list[CheckSpec] = []
    for s in BRONZE_SOURCES:
        out.append(
            CheckSpec(
                f"bronze_{s}_minutes_since_last_ingest",
                f"SELECT (unix_timestamp(current_timestamp()) - unix_timestamp(max(ingested_at))) / 60.0 FROM {bronze(s)}",
                "the ingest job runs every 30 minutes; a table this stale means runs were missed or failed",
                maximum=MAX_MINUTES_SINCE_INGEST,
            )
        )
        out.append(
            CheckSpec(
                f"bronze_{s}_minutes_since_newest_snapshot",
                f"SELECT (unix_timestamp(current_timestamp()) - max(feed_header_ts)) / 60.0 FROM {bronze(s)}",
                "age of the newest feed snapshot the collector took; stale means the collector stopped or ingest is behind",
                maximum=MAX_MINUTES_SINCE_LAST_SNAPSHOT,
            )
        )
        out.append(
            CheckSpec(
                f"bronze_{s}_snapshots_in_a_full_hour",
                f"SELECT count(DISTINCT feed_header_ts) FROM {bronze(s)} WHERE feed_header_ts >= unix_timestamp(date_trunc('HOUR', current_timestamp() - INTERVAL 3 HOURS)) AND feed_header_ts < unix_timestamp(date_trunc('HOUR', current_timestamp() - INTERVAL 2 HOURS))",
                "distinct snapshots in a full hour 2 to 3 hours ago (old enough to be ingested); a drop means the collector missed pulls",
                minimum=MIN_SNAPSHOTS_PER_HOUR[s],
            )
        )
    out.append(
        CheckSpec(
            "bronze_vehicle_positions_longest_gap_seconds_6h",
            f"WITH t AS (SELECT DISTINCT feed_header_ts AS ts FROM {bronze('vehicle_positions')} WHERE feed_header_ts >= unix_timestamp(current_timestamp() - INTERVAL 6 HOURS)) SELECT max(ts - prev) FROM (SELECT ts, lag(ts) OVER (ORDER BY ts) AS prev FROM t)",
            "longest silence between vehicle snapshots in the last 6 hours (the nightly empty-feed window is about 11 minutes)",
            maximum=MAX_SNAPSHOT_GAP_S,
        )
    )
    out += [
        CheckSpec(
            "silver_vehicle_positions_hours_behind_bronze",
            f"SELECT (unix_timestamp((SELECT max(ingested_at) FROM {bronze('vehicle_positions')})) - unix_timestamp(greatest((SELECT max(bronze_ingested_at) FROM {silver}.vehicle_positions), (SELECT max(bronze_ingested_at) FROM {silver}.quarantine_vehicle_positions)))) / 3600.0",
            "the daily silver job should keep silver within a day of bronze (this is the 2026-10-16 incident, watched hourly)",
            maximum=MAX_HOURS_SILVER_BEHIND,
        ),
        CheckSpec(
            "gold_trip_performance_days_behind",
            f"SELECT datediff({PACIFIC_DATE}, max(service_date)) FROM {gold}.trip_performance",
            "the newest service date in gold; more than 2 days behind means the daily job stopped producing it",
            maximum=MAX_DAYS_GOLD_BEHIND,
        ),
        CheckSpec(
            "scheduled_trips_today",
            f"SELECT count(*) FROM {silver}.scheduled_trips WHERE service_date = {PACIFIC_DATE}",
            "the schedule must be expanded for today or nothing can be matched",
            minimum=MIN_TRIPS_PER_DAY,
        ),
        CheckSpec(
            "schedule_expanded_days_ahead_of_today",
            f"SELECT datediff(max(service_date), {PACIFIC_DATE}) FROM {silver}.scheduled_trips",
            "the daily job expands trips through the day after its run, so the newest expanded date must never be behind today in Seattle (0 = through today, 1 = through tomorrow); a negative value means the daily job missed a run",
            minimum=0,
        ),
        CheckSpec(
            "weather_minutes_since_last_fetch",
            f"SELECT (unix_timestamp(current_timestamp()) - max({fetched_at})) / 60.0 FROM {files}",
            "age of the newest landed weather file (the fetch runs hourly from GitHub, started by the Cloudflare dispatcher); a large value means the fetch stopped. Reads the landing files, not the daily silver table",
            maximum=MAX_MINUTES_SINCE_WEATHER_FETCH,
        ),
        CheckSpec(
            "weather_stations_fetched_last_3_hours",
            f"SELECT count(DISTINCT {station}) FROM {files} WHERE {fetched_at} >= unix_timestamp(current_timestamp()) - 3 * 3600",
            "at least two of the three Seattle-area stations should have a landed file from the last 3 hours",
            minimum=MIN_WEATHER_STATIONS_3H,
        ),
        CheckSpec(
            "weather_silver_hours_behind",
            f"SELECT (unix_timestamp(current_timestamp()) - unix_timestamp(max(observed_ts))) / 3600.0 FROM {silver}.weather_observations",
            "silver weather is rebuilt once a day by the silver job (11:45 UTC), so it is normally up to about a day old; more than 30 hours means a daily run was missed",
            maximum=MAX_HOURS_WEATHER_SILVER_BEHIND,
        ),
        CheckSpec(
            "days_of_schedule_left",
            f"SELECT datediff(to_date(max(feed_end), 'yyyyMMdd'), {PACIFIC_DATE}) FROM {silver}.gtfs_feeds",
            "Metro's schedule has an end date; if no newer feed is loaded before then, nothing will match",
            minimum=MIN_DAYS_OF_SCHEDULE_LEFT,
        ),
    ]
    return out
