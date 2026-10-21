"""Pure parsing of National Weather Service observations (no Spark, no network).

An observation is one GeoJSON feature from `api.weather.gov/stations/<id>/observations`. Rain
AMOUNTS are not available from this service (the precipitation fields were empty in every
observation checked on 2026-10-21), so wetness is read from the reported present-weather codes:
rain, drizzle, showers, snow, thunderstorms, with the light, moderate or heavy intensity the
station reports.
"""

import datetime as dt
import json
from collections.abc import Iterable, Mapping
from typing import Any

PRECIP_WORDS = (
    "rain",
    "drizzle",
    "shower",
    "snow",
    "thunder",
    "hail",
    "sleet",
    "freezing",
    "ice_pellets",
)
FOG_WORDS = ("fog",)  # the service names mist and fog together as "fog_mist"
INTENSITY = {"light": 1, "heavy": 3}  # no intensity reported means moderate (2)
WET_SHARE_FOR_RAIN = (
    0.25  # an hour is "rain" when over a quarter of its observations report precipitation
)
FOG_SHARE_FOR_FOG = 0.25


def _value(properties: Mapping[str, Any], key: str) -> float | None:
    node = properties.get(key)
    value = node.get("value") if isinstance(node, Mapping) else None
    return None if value is None else float(value)


def precipitation_level(present_weather: Iterable[Mapping[str, Any]] | None) -> int:
    """0 none, 1 light, 2 moderate, 3 heavy: the strongest precipitation code reported."""

    level = 0
    for item in present_weather or []:
        weather = str(item.get("weather") or "")
        if any(word in weather for word in PRECIP_WORDS):
            level = max(level, INTENSITY.get(str(item.get("intensity") or ""), 2))
    return level


def is_fog(present_weather: Iterable[Mapping[str, Any]] | None) -> bool:
    return any(
        any(word in str(item.get("weather") or "") for word in FOG_WORDS)
        for item in present_weather or []
    )


def parse_observation(feature: Mapping[str, Any]) -> dict[str, Any] | None:
    """One row per observation, or None when it has no usable timestamp or station."""

    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        return None
    stamp = properties.get("timestamp")
    station = str(
        properties.get("stationId") or str(properties.get("station") or "").rsplit("/", 1)[-1]
    )
    if not stamp or not station:
        return None
    observed = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(dt.UTC)
    present = properties.get("presentWeather") or []
    return {
        "station": station,
        "observed_ts": observed.replace(tzinfo=None),
        "temp_c": _value(properties, "temperature"),
        "wind_kmh": _value(properties, "windSpeed"),
        "gust_kmh": _value(properties, "windGust"),
        "visibility_m": _value(properties, "visibility"),
        "humidity_pct": _value(properties, "relativeHumidity"),
        "pressure_pa": _value(properties, "barometricPressure"),
        "text": properties.get("textDescription") or None,
        "precip_level": precipitation_level(present),
        "fog": is_fog(present),
        "codes": ",".join(
            f"{item.get('intensity') or 'moderate'}:{item.get('weather')}" for item in present
        )
        or None,
    }


def parse_file(text: str) -> list[dict[str, Any]]:
    """Rows of one landed file (a station and its features); unusable features are skipped."""

    rows = []
    for feature in json.loads(text).get("features", []):
        row = parse_observation(feature)
        if row is not None:
            rows.append(row)
    return rows


def hour_class(wet_share: float, fog_share: float) -> str:
    """rain, fog, or dry, for one hour (SQL twin in `silver_weather.hourly_sql`)."""

    if wet_share >= WET_SHARE_FOR_RAIN:
        return "rain"
    if fog_share >= FOG_SHARE_FOR_FOG:
        return "fog"
    return "dry"
