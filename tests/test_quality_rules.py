from typing import Any

from transit_reliability.quality.rules import quarantine_reason

GOOD: dict[str, Any] = {
    "vehicle_id": "4316",
    "trip_id": "562519821",
    "vehicle_ts": 1_000_000,
    "feed_header_ts": 1_000_030,
    "latitude": 47.6,
    "longitude": -122.3,
}


def _with(**changes: Any) -> dict[str, Any]:
    return {**GOOD, **changes}


def test_a_normal_fix_passes() -> None:
    assert quarantine_reason(GOOD) is None


def test_placeholder_row_with_no_timestamp_is_caught_first() -> None:
    placeholder = _with(vehicle_ts=None, latitude=0.0, longitude=0.0)
    assert quarantine_reason(placeholder) == "NO_VEHICLE_TS"
    assert quarantine_reason(_with(vehicle_ts=0)) == "NO_VEHICLE_TS"


def test_zero_or_missing_coordinates() -> None:
    assert quarantine_reason(_with(latitude=0.0)) == "ZERO_COORDINATES"
    assert quarantine_reason(_with(longitude=None)) == "ZERO_COORDINATES"


def test_coordinates_outside_king_county() -> None:
    assert quarantine_reason(_with(latitude=40.7, longitude=-74.0)) == "OUT_OF_BOUNDS"


def test_timestamps_far_from_the_feed_time() -> None:
    assert quarantine_reason(_with(vehicle_ts=1_000_100)) == "TS_IN_FUTURE"
    assert quarantine_reason(_with(vehicle_ts=1_000_030 + 60)) is None  # exactly at the limit
    assert quarantine_reason(_with(vehicle_ts=1_000_030 - 3601)) == "TS_STALE"
    assert quarantine_reason(_with(vehicle_ts=1_000_030 - 3600)) is None


def test_missing_identifiers() -> None:
    assert quarantine_reason(_with(vehicle_id="")) == "MISSING_VEHICLE_ID"
    assert quarantine_reason(_with(trip_id=None)) == "MISSING_TRIP_ID"
