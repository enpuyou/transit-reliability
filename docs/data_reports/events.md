# Events

Status: **Planned**
Source role: scheduled demand and disruption context
Expected cadence: daily plus a maintained seed file
Sources: event APIs, venue/team sites, and reviewed manual entries

## Executive summary

Events provide a calendar of games, concerts, festivals, and other demand shocks. They can
help explain unusual crowding, delay, or post-event route conditions.

## Example record

Representative shape:

```text
event_id: mlb-2026-10-13-seattle
venue: <venue>
start_time: 2026-10-13T19:10:00-07:00
end_time: 2026-10-13T22:30:00-07:00
attendance_tier: high
```

Event times and attendance tiers must be reviewed for correctness and timezone handling.

## What it enables

- Event-day versus normal-day comparisons.
- Route and segment context near venues.
- Prediction features for known demand windows.
- Planner briefs about recurring post-event conditions.

## Engineering and quality notes

Retain the source URL, retrieval date, timezone, venue coordinates, and confidence of any
attendance estimate. Event proximity is an association flag, not causal proof.

## Product story

Events help answer: **does this corridor behave differently when a predictable large crowd
leaves, and should service planning account for it?**
