"""Row quality rules for realtime vehicle positions.

These constants and `quarantine_reason` are the single definition of the rules. The Spark
expression in `silver_realtime.py` is built from the same constants and ordering, and its
counts are checked against this reference in dev (see decision 0007).
"""

from collections.abc import Mapping
from typing import Any, Final

# Generous box around King County: (lat_min, lat_max, lon_min, lon_max).
KING_COUNTY_BOX: Final = (46.9, 48.1, -122.8, -121.0)
MAX_FUTURE_SECONDS: Final = 60  # a fix may be at most this far ahead of the feed time
MAX_STALE_SECONDS: Final = 3600  # a fix older than this versus the feed time is stale

# First matching rule wins, so each bad row has exactly one reason.
REASONS: Final = (
    "NO_VEHICLE_TS",
    "ZERO_COORDINATES",
    "OUT_OF_BOUNDS",
    "TS_IN_FUTURE",
    "TS_STALE",
    "MISSING_VEHICLE_ID",
    "MISSING_TRIP_ID",
)


def quarantine_reason(row: Mapping[str, Any]) -> str | None:
    """Why a bronze vehicle-position row is unusable, or None when it is fine."""

    ts = row.get("vehicle_ts")
    feed = row.get("feed_header_ts")
    lat, lon = row.get("latitude"), row.get("longitude")
    lat_min, lat_max, lon_min, lon_max = KING_COUNTY_BOX
    if ts is None or ts == 0:
        return "NO_VEHICLE_TS"
    if lat is None or lon is None or lat == 0 or lon == 0:
        return "ZERO_COORDINATES"
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        return "OUT_OF_BOUNDS"
    if feed is not None and ts > feed + MAX_FUTURE_SECONDS:
        return "TS_IN_FUTURE"
    if feed is not None and ts < feed - MAX_STALE_SECONDS:
        return "TS_STALE"
    if not row.get("vehicle_id"):
        return "MISSING_VEHICLE_ID"
    if not row.get("trip_id"):
        return "MISSING_TRIP_ID"
    return None
