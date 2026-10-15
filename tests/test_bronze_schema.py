import pytest

from transit_reliability.bronze.schema import (
    ALERT_COLUMNS,
    SOURCE_TO_COLUMNS,
    TRIP_UPDATE_COLUMNS,
    VEHICLE_POSITION_COLUMNS,
    table_name,
)


def test_bronze_tables_have_source_and_ingestion_metadata() -> None:
    for columns in (VEHICLE_POSITION_COLUMNS, TRIP_UPDATE_COLUMNS, ALERT_COLUMNS):
        assert "entity_id" in columns
        assert "feed_header_ts" in columns
        assert "source_file" in columns
        assert "ingested_at" in columns


def test_all_supported_sources_map_to_bronze_tables() -> None:
    assert set(SOURCE_TO_COLUMNS) == {"vehicle_positions", "trip_updates", "alerts"}
    assert table_name("dev_transit", "vehicle_positions") == (
        "workspace.dev_transit_bronze.vehicle_positions"
    )


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported bronze source"):
        table_name("dev_transit", "unknown")


def test_volume_path_converts_dotted_name() -> None:
    from transit_reliability.bronze.schema import volume_path

    assert volume_path("workspace.transit_landing.raw", "alerts") == (
        "/Volumes/workspace/transit_landing/raw/alerts"
    )
    with pytest.raises(ValueError, match="catalog.schema.volume"):
        volume_path("raw", "alerts")


def test_parse_record_matches_expected_fixture_counts() -> None:
    from pathlib import Path

    from transit_reliability.bronze.streaming import PARSED_COLUMNS, parse_record

    fixtures = Path(__file__).parent / "fixtures"
    expected = {"vehicle_positions": 522, "trip_updates": 33772, "alerts": 81}
    names = {
        "vehicle_positions": "vehiclepositions.pb",
        "trip_updates": "tripupdates.pb",
        "alerts": "alerts.pb",
    }
    for source, count in expected.items():
        rows = parse_record(source, "f.pb", (fixtures / names[source]).read_bytes())
        assert len(rows) == count
        assert set(rows[0]) == set(PARSED_COLUMNS[source])


def test_trip_level_schedule_relationship_is_a_bronze_column_for_both_realtime_feeds() -> None:
    assert "trip_schedule_relationship" in VEHICLE_POSITION_COLUMNS
    assert "trip_schedule_relationship" in TRIP_UPDATE_COLUMNS
    # The new column sits before source_file and ingested_at, which stay last.
    assert TRIP_UPDATE_COLUMNS[-2:] == ("source_file", "ingested_at")
    assert VEHICLE_POSITION_COLUMNS[-2:] == ("source_file", "ingested_at")


def test_missing_columns_lists_only_what_a_table_lacks_with_types() -> None:
    from transit_reliability.bronze.schema import missing_columns
    from transit_reliability.bronze.streaming import column_ddl_types

    old_table = [c for c in TRIP_UPDATE_COLUMNS if c != "trip_schedule_relationship"]
    assert missing_columns(old_table, column_ddl_types("trip_updates")) == [
        ("trip_schedule_relationship", "bigint")
    ]
    assert missing_columns(TRIP_UPDATE_COLUMNS, column_ddl_types("trip_updates")) == []


def test_every_bronze_column_has_a_sql_type() -> None:
    from transit_reliability.bronze.streaming import column_ddl_types

    for source, columns in SOURCE_TO_COLUMNS.items():
        types = dict(column_ddl_types(source))
        assert list(types) == list(columns)
        assert types["ingested_at"] == "timestamp"
    assert dict(column_ddl_types("alerts"))["active_periods"] == "array<string>"
