# NOAA Weather

Status: **Planned**
Source role: weather context for delay analysis
Format: weather.gov JSON API
Cadence: hourly observations

## Executive summary

NOAA observations can tell us what weather conditions coincided with transit performance:
rain, temperature, wind, visibility, and related observations. This supports comparison of
normal, rainy, windy, or unusually cold periods. It does not prove weather caused a delay.

## Example record

Representative shape:

```json
{
  "station": "KSEA",
  "timestamp": "2026-10-13T19:00:00+00:00",
  "temperature_c": 12.8,
  "wind_speed": "13 km/h",
  "precipitation_last_hour": "2.3 mm",
  "visibility_m": 16000
}
```

The actual station, fields, units, and observation coverage must be profiled before use.

## What it enables

- Compare route delay distributions in dry versus rainy periods.
- Add weather features to prediction models.
- Explain broad system-wide disruptions with time and location context.
- Show weather alongside route reliability charts.

## Engineering and quality notes

Store station ID, observation timestamp, retrieval timestamp, raw response, and normalized
units. Join observations to route segments by time and a documented spatial radius. Handle
missing observations and do not infer causality from correlation.

## Product story

A planner can ask whether a delay pattern is unusual for ordinary conditions or part of a
repeatable weather-related operating problem. Weather should be presented as context, not
as an automatic explanation.
