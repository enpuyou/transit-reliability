import datetime as dt
from pathlib import Path

from transit_reliability.incidents.parse import category, local_to_utc, parse_incidents

SAMPLE = Path("tests/fixtures/incidents_sample.json").read_text()


def test_categories() -> None:
    assert category("MVI - Motor Vehicle Incident") == "traffic"
    assert category("MVI Medic") == "traffic"
    assert category("Car Fire") == "traffic"
    assert category("Fire in Building") == "fire"
    assert category("Auto Fire Alarm") == "other"
    assert category("Aid Response") == "medical"
    assert category(None) == "other"


def test_seattle_local_time_becomes_utc_across_daylight_saving() -> None:
    assert local_to_utc("2026-10-22T13:12:00.000") == dt.datetime(
        2026, 10, 22, 20, 12, tzinfo=dt.UTC
    )  # PDT, UTC-7
    assert local_to_utc("2026-12-01T13:12:00.000") == dt.datetime(
        2026, 12, 1, 21, 12, tzinfo=dt.UTC
    )  # PST, UTC-8


def test_parse_the_sample() -> None:
    rows = parse_incidents(
        SAMPLE, "1792700000000-incidents.json", dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
    )
    assert len(rows) == 5 and all(r["incident_number"] for r in rows)
    assert [r["category"] for r in rows][:3] == ["traffic", "traffic", "fire"]
    assert all(r["dispatched_ts"].tzinfo is not None for r in rows)
