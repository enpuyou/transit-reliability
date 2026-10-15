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


def _feed_with_trip_update(relationship: int, stop_count: int) -> bytes:
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_000
    entity = feed.entity.add()
    entity.id = "e1"
    entity.trip_update.trip.trip_id = "T1"
    entity.trip_update.trip.schedule_relationship = relationship
    for sequence in range(1, stop_count + 1):
        stop = entity.trip_update.stop_time_update.add()
        stop.stop_sequence = sequence
        stop.arrival.delay = 30
    return bytes(feed.SerializeToString())


def test_trip_updates_keep_the_trip_level_cancellation_flag_on_every_row() -> None:
    rows = parse_trip_updates(_feed_with_trip_update(relationship=3, stop_count=2))  # CANCELED

    assert [row["trip_schedule_relationship"] for row in rows] == [3, 3]
    assert [row["stop_sequence"] for row in rows] == [1, 2]


def test_a_trip_update_with_no_stops_still_yields_one_trip_only_row() -> None:
    rows = parse_trip_updates(_feed_with_trip_update(relationship=3, stop_count=0))

    assert len(rows) == 1
    assert rows[0]["trip_id"] == "T1"
    assert rows[0]["trip_schedule_relationship"] == 3
    assert rows[0]["stop_sequence"] is None and rows[0]["arrival_delay"] is None


def test_vehicle_positions_keep_the_trip_level_schedule_relationship() -> None:
    rows = parse_vehicle_positions(fixture("vehiclepositions"))

    assert "trip_schedule_relationship" in rows[0]
    assert rows[0]["trip_schedule_relationship"] in (0, None)  # SCHEDULED, or not set


def test_fixture_trip_updates_carry_the_new_column_for_every_row() -> None:
    rows = parse_trip_updates(fixture("tripupdates"))

    assert len(rows) == 33772  # the trip-only fallback must not add rows to normal feeds
    assert all("trip_schedule_relationship" in row for row in rows)
