# Seattle Fire 911 Dispatches

Status: **Planned**
Source role: public incident context
Format: Socrata dataset `kzjm-xkqj`
Cadence: approximately 5 minutes

## Executive summary

Seattle Fire dispatch records can identify incidents that occurred near a route or stop at
roughly the same time as a delay. They can enrich a delay timeline, but dispatch proximity
does not establish that an incident caused a specific bus delay.

## Example record

Representative shape:

```text
incident_id: <dispatch id>
received_at: 2026-10-13T19:05:00Z
incident_type: <incident category>
latitude: 47.60
longitude: -122.33
status: <dispatch status>
```

The exact public fields and privacy handling must be profiled before ingestion.

## What it enables

- Incident markers near route shapes and stops.
- Delay windows before and after nearby dispatches.
- Incident-category summaries by corridor.
- Supporting evidence for a planner investigation.

## Engineering and quality notes

Use the Socrata API with incremental time filters, retain retrieval timestamps, normalize
coordinates, and document the spatial buffer and active time window. Avoid publishing
sensitive details or treating all dispatches as traffic incidents.

## Product story

This source helps a planner investigate exceptional delay days and distinguish a possible
one-off incident from recurring schedule or roadway problems.
