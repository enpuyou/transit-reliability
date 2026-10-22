import datetime as dt
from pathlib import Path

from transit_reliability.events.parse import parse_events, pulled_at_from_name

SAMPLE = Path("tests/fixtures/events_sample.json").read_text()


def test_parse_events_reads_the_sample() -> None:
    rows = parse_events(SAMPLE, "1792700000000-p0.json", dt.datetime(2026, 10, 22, tzinfo=dt.UTC))
    assert len(rows) == 4
    kraken = rows[0]
    assert kraken["name"] == "Seattle Kraken vs. Utah Mammoth"
    assert kraken["venue_name"] == "Climate Pledge Arena" and kraken["large_venue"] is True
    assert kraken["start_ts"] == dt.datetime(2026, 10, 23, 2, 0, tzinfo=dt.UTC)  # 7:00 pm Seattle
    assert abs(kraken["latitude"] - 47.622) < 0.01 and abs(kraken["longitude"] + 122.354) < 0.01
    assert kraken["segment"] == "Sports" and kraken["status"] == "onsale"
    assert all(r["event_id"] for r in rows)


def test_no_api_key_in_the_sample_and_file_names_give_the_pull_time() -> None:
    assert "apikey=REMOVED" in SAMPLE or "apikey" not in SAMPLE
    assert pulled_at_from_name("1792700000000-p1.json") == dt.datetime.fromtimestamp(
        1792700000, dt.UTC
    )


def test_events_payload_groups_routes_per_event() -> None:
    from transit_reliability.public.map_data import events_payload

    start = dt.datetime(2026, 10, 23, 2, 0, tzinfo=dt.UTC)
    rows = [
        (
            "e1",
            "Kraken",
            "Sports",
            start,
            "onsale",
            "Climate Pledge Arena",
            47.622,
            -122.354,
            True,
            18000,
            "100001",
            120,
        ),
        (
            "e1",
            "Kraken",
            "Sports",
            start,
            "onsale",
            "Climate Pledge Arena",
            47.622,
            -122.354,
            True,
            18000,
            "100008",
            300,
        ),
    ]
    out = events_payload(rows)
    assert len(out) == 1 and out[0]["routes"] == {"100001": 120, "100008": 300}
    assert out[0]["start"] == int(start.timestamp()) and out[0]["large"] is True


def test_silver_keeps_the_newest_version_of_each_event() -> None:
    from transit_reliability.events.silver_events import latest_sql

    sql = latest_sql("landed_events")
    assert "PARTITION BY event_id" in sql and "pulled_at DESC" in sql and "rn = 1" in sql


def test_venue_names_match_whatever_the_capitals() -> None:
    from transit_reliability.events.parse import venue_seats

    assert venue_seats("accesso ShoWare Center") == 7000
    assert venue_seats("The 5th Avenue Theatre") == 2100
    assert venue_seats("Tractor") is None
