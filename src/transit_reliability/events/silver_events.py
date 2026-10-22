"""Silver events and venues: the newest version of every event the daily Ticketmaster pull has
landed, and one row per venue (its name, location and size are stored once).

Each daily pull lands the next three weeks of events again, so the same event appears in many files;
an event can also change (new time, cancelled, postponed). The table keeps one row per event: the
one from the newest pull. Rebuilt whole each run (a few thousand rows at most).
"""

from typing import Any

from transit_reliability.bronze.schema import volume_path
from transit_reliability.events.parse import parse_events, pulled_at_from_name

EVENTS = "events"
VENUES = "venues"
DDL = (
    "event_id string, name string, segment string, genre string, start_ts timestamp, "
    "local_date string, status string, venue_id string, venue_name string, latitude double, "
    "longitude double, venue_seats int, large_venue boolean, source_file string, "
    "pulled_at timestamp"
)
COLUMNS = [c.strip().split(" ")[0] for c in DDL.split(",")]


def latest_sql(view: str) -> str:
    """One row per event: the version from the newest pull (ties broken by file name)."""

    return (
        f"SELECT * EXCEPT (rn) FROM (SELECT *, row_number() OVER (PARTITION BY event_id "
        f"ORDER BY pulled_at DESC, source_file DESC) AS rn FROM {view} WHERE event_id IS NOT NULL) "
        f"WHERE rn = 1"
    )


def venues_sql(prefix: str) -> str:
    """One row per venue: newest name and location, size (when known), how many events we saw."""

    events = f"workspace.{prefix}_silver.{EVENTS}"
    return f"""SELECT venue_id, max_by(venue_name, pulled_at) AS venue_name,
        max_by(latitude, pulled_at) AS latitude, max_by(longitude, pulled_at) AS longitude,
        max(venue_seats) AS venue_seats, max(large_venue) AS large_venue,
        count(*) AS n_events, min(start_ts) AS first_event, max(start_ts) AS last_event
        FROM {events} WHERE venue_id IS NOT NULL GROUP BY venue_id"""


def build_events(spark: Any, prefix: str, landing_volume: str) -> None:
    root = volume_path(landing_volume, "events")
    try:
        files = (
            spark.read.format("binaryFile")
            .option("recursiveFileLookup", "true")
            .load(root)
            .select("path", "content")
            .collect()
        )
    except Exception as error:  # noqa: BLE001 - nothing landed yet is not a failure of this step
        print(f"no event files under {root} yet ({str(error)[:120]}); nothing to build")
        return
    rows: list[tuple[Any, ...]] = []
    for f in files:
        name = f["path"].split("/")[-1]
        for r in parse_events(bytes(f["content"]).decode("utf-8"), name, pulled_at_from_name(name)):
            rows.append(tuple(r[c] for c in COLUMNS))
    print(f"event files: {len(files)}, event rows: {len(rows)}")
    if not rows:
        return
    spark.createDataFrame(rows, DDL).createOrReplaceTempView("landed_events")
    target = f"workspace.{prefix}_silver.{EVENTS}"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {latest_sql('landed_events')}")
    print(f"{target}: {spark.table(target).count()} events")
    venues = f"workspace.{prefix}_silver.{VENUES}"
    spark.sql(f"CREATE OR REPLACE TABLE {venues} AS {venues_sql(prefix)}")
    print(f"{venues}: {spark.table(venues).count()} venues")
