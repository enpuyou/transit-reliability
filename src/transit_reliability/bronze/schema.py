"""Bronze table contracts shared by local tests and Spark ingestion."""

from typing import Final

VEHICLE_POSITION_COLUMNS: Final[tuple[str, ...]] = (
    "entity_id",
    "feed_header_ts",
    "vehicle_id",
    "vehicle_label",
    "trip_id",
    "route_id",
    "direction_id",
    "start_date",
    "start_time",
    "latitude",
    "longitude",
    "bearing",
    "speed",
    "current_stop_sequence",
    "stop_id",
    "current_status",
    "vehicle_ts",
    "occupancy_status",
    "occupancy_percentage",
    "source_file",
    "ingested_at",
)

TRIP_UPDATE_COLUMNS: Final[tuple[str, ...]] = (
    "entity_id",
    "feed_header_ts",
    "trip_update_ts",
    "trip_id",
    "route_id",
    "direction_id",
    "start_date",
    "start_time",
    "vehicle_id",
    "stop_sequence",
    "stop_id",
    "arrival_delay",
    "arrival_time",
    "departure_delay",
    "departure_time",
    "schedule_relationship",
    "source_file",
    "ingested_at",
)

ALERT_COLUMNS: Final[tuple[str, ...]] = (
    "entity_id",
    "feed_header_ts",
    "cause",
    "effect",
    "severity_level",
    "header_text",
    "description_text",
    "url",
    "active_periods",
    "informed_entities",
    "source_file",
    "ingested_at",
)

SOURCE_TO_TABLE: Final[dict[str, str]] = {
    "vehicle_positions": "vehicle_positions",
    "trip_updates": "trip_updates",
    "alerts": "alerts",
}

SOURCE_TO_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "vehicle_positions": VEHICLE_POSITION_COLUMNS,
    "trip_updates": TRIP_UPDATE_COLUMNS,
    "alerts": ALERT_COLUMNS,
}


def volume_path(volume: str, *parts: str) -> str:
    """Return the /Volumes file path for a catalog.schema.volume name."""

    pieces = volume.split(".")
    if len(pieces) != 3 or not all(pieces):
        raise ValueError(f"Volume must be catalog.schema.volume, got: {volume}")
    return "/".join(["/Volumes", *pieces, *parts])


def table_name(schema_prefix: str, source: str) -> str:
    """Return the fully qualified bronze table for a supported source."""

    try:
        table = SOURCE_TO_TABLE[source]
    except KeyError as error:
        raise ValueError(f"Unsupported bronze source: {source}") from error
    return f"workspace.{schema_prefix}_bronze.{table}"
