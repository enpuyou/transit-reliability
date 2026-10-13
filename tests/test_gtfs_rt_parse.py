from pathlib import Path

from transit_reliability.gtfs_rt.parse import (
    parse_alerts,
    parse_trip_updates,
    parse_vehicle_positions,
)


def fixture(name: str) -> bytes:
    return Path(f"tests/fixtures/{name}.pb").read_bytes()


def test_parse_vehicle_positions_preserves_location_and_stop_fields() -> None:
    rows = parse_vehicle_positions(fixture("vehiclepositions"))

    assert len(rows) == 522
    assert rows[0]["vehicle_id"] == "4316"
    assert rows[0]["trip_id"] == "562519421"
    assert rows[0]["stop_id"] == "2020"
    assert rows[0]["latitude"] == 47.645233154296875
    assert rows[0]["current_status"] == 1


def test_parse_trip_updates_explodes_stop_events() -> None:
    rows = parse_trip_updates(fixture("tripupdates"))

    assert len(rows) == 33772
    assert rows[0]["trip_id"] == "562519331"
    assert rows[0]["stop_sequence"] == 1
    assert rows[0]["stop_id"] == "2010"
    assert rows[0]["arrival_delay"] == 0
    assert rows[0]["arrival_time"] == 1791921360


def test_parse_alerts_preserves_text_periods_and_entities() -> None:
    rows = parse_alerts(fixture("alerts"))

    assert len(rows) == 81
    assert rows[0]["entity_id"] == "82885"
    assert rows[0]["header_text"]
    assert rows[0]["active_periods"]
    assert rows[0]["informed_entities"]
