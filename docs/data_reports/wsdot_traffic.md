# WSDOT Traffic

Status: **Planned**
Source role: roadway congestion and travel-time context
Format: WSDOT Traveler Information API
Cadence: approximately 2 to 5 minutes for selected endpoints
Access: access code required for relevant services

## Executive summary

WSDOT traffic data can show whether buses were operating near congested roads or highway
segments. It is especially useful for explaining recurring corridor delay and comparing bus
performance with roadway conditions.

## Example record

Representative shape:

```json
{
  "station_id": "<traffic-station>",
  "observed_at": "2026-10-13T19:05:00Z",
  "roadway": "<roadway>",
  "direction": "northbound",
  "speed_mph": 18,
  "reference_speed_mph": 45,
  "status": "stop_and_go"
}
```

The endpoint-specific schema and access limits must be verified when the API code is
available.

## What it enables

- Join congestion conditions to nearby route segments.
- Compare scheduled runtime with runtime during stop-and-go traffic.
- Add traffic features to prediction models.
- Support a planner story about corridor-level schedule padding.

## Engineering and quality notes

Keep raw responses and API timestamps. Map stations and road segments to transit shapes
using a documented distance threshold. Avoid treating nearby highway conditions as proof
of bus delay on a parallel local street.

## Product story

This source helps answer: **is the bus late because the corridor itself is slow, and is
that recurring enough to change the schedule?** It is supporting evidence, not a causal
experiment.
