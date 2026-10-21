import json
from typing import Any

from transit_reliability.weather.parse import (
    hour_class,
    is_fog,
    parse_file,
    parse_observation,
    precipitation_level,
)


def feature(**over: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "stationId": "KSEA",
        "timestamp": "2026-10-17T20:53:00+00:00",
        "temperature": {"value": 12.2, "unitCode": "wmoUnit:degC"},
        "windSpeed": {"value": 18.5},
        "windGust": {"value": None},
        "visibility": {"value": 16090},
        "relativeHumidity": {"value": 91.2},
        "barometricPressure": {"value": 101800},
        "textDescription": "Light Rain",
        "presentWeather": [
            {"intensity": "light", "modifier": None, "weather": "rain", "rawString": "-RA"}
        ],
    }
    props.update(over)
    return {"properties": props}


def test_a_typical_observation_becomes_one_clean_row() -> None:
    row = parse_observation(feature())
    assert row is not None
    assert row["station"] == "KSEA" and str(row["observed_ts"]) == "2026-10-17 20:53:00"
    assert row["temp_c"] == 12.2 and row["gust_kmh"] is None and row["visibility_m"] == 16090.0
    assert row["precip_level"] == 1 and row["fog"] is False and row["codes"] == "light:rain"


def test_precipitation_intensity_defaults_to_moderate_and_takes_the_strongest_code() -> None:
    assert precipitation_level([{"intensity": None, "weather": "rain"}]) == 2
    assert (
        precipitation_level(
            [{"intensity": "heavy", "weather": "rain"}, {"intensity": "light", "weather": "snow"}]
        )
        == 3
    )
    assert (
        precipitation_level([{"intensity": "light", "weather": "fog_mist"}]) == 0
    )  # fog is not precipitation
    assert precipitation_level(None) == 0 and precipitation_level([]) == 0


def test_mist_and_fog_are_recognised_and_clear_weather_is_neither() -> None:
    assert is_fog([{"weather": "fog_mist", "rawString": "BR"}])
    assert not is_fog([{"weather": "rain"}]) and not is_fog(None)
    row = parse_observation(feature(presentWeather=[], textDescription="Clear"))
    assert row is not None and row["precip_level"] == 0 and row["codes"] is None


def test_missing_measurements_stay_missing_and_unusable_features_are_skipped() -> None:
    row = parse_observation(feature(temperature={"value": None}, windSpeed=None))
    assert row is not None and row["temp_c"] is None and row["wind_kmh"] is None
    assert parse_observation({"properties": {"stationId": "KSEA"}}) is None  # no timestamp
    assert (
        parse_observation({"properties": {"timestamp": "2026-10-17T20:53:00+00:00"}}) is None
    )  # no station
    assert parse_observation({}) is None


def test_the_station_is_taken_from_its_url_when_there_is_no_station_id() -> None:
    row = parse_observation(
        feature(stationId=None, station="https://api.weather.gov/stations/KBFI")
    )
    assert row is not None and row["station"] == "KBFI"


def test_a_landed_file_parses_every_usable_observation() -> None:
    text = json.dumps(
        {
            "station": "KSEA",
            "features": [
                feature(),
                {"properties": {}},
                feature(timestamp="2026-10-17T21:53:00+00:00"),
            ],
        }
    )
    assert len(parse_file(text)) == 2


def test_an_hour_is_rain_fog_or_dry_by_the_share_of_its_observations() -> None:
    assert hour_class(0.25, 0.0) == "rain" and hour_class(0.3, 0.9) == "rain"  # rain outranks fog
    assert hour_class(0.1, 0.25) == "fog" and hour_class(0.24, 0.24) == "dry"


def test_the_hourly_sql_uses_the_same_thresholds_as_the_pure_rule() -> None:
    from transit_reliability.weather.parse import FOG_SHARE_FOR_FOG, WET_SHARE_FOR_RAIN
    from transit_reliability.weather.silver_weather import hourly_sql

    sql = hourly_sql("transit")
    assert (
        f">= {WET_SHARE_FOR_RAIN} THEN 'rain'" in sql
        and f">= {FOG_SHARE_FOR_FOG} THEN 'fog'" in sql
    )
    assert "workspace.transit_silver.weather_observations" in sql and "America/Los_Angeles" in sql
