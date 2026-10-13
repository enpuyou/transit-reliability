# Ridership

| | |
|---|---|
| Status | **Planned** |
| Phase | 6 |
| Owner | King County Metro |
| Format | to be verified |
| Cadence | per service change or periodic dashboard release |
| Profiled | not yet |
| Target tables | ridership weighting table |

## Executive summary

Ridership turns operational delay into rider impact. A two-minute delay on a lightly used
route and a two-minute delay on a busy corridor are not equivalent from a planning
perspective.

## Example record

Representative shape:

```text
route_id: <route>
stop_id: <stop>
service_date_type: weekday
hour_band: 08:00-09:00
estimated_boardings: 420
source_period: <report period>
```

The actual grain, privacy aggregation, and availability of stop-level values must be
verified before modeling.

## What we can do with this source

- Estimated rider-minutes lost.
- Rider-weighted route and stop rankings.
- Prioritization of schedule recommendations.
- Comparison of raw delay with customer impact.

## Engineering plan and quality checks

Keep source period and aggregation grain. Never present estimated boardings as real-time
passenger counts. Document the formula and uncertainty for any rider-minute estimate.

## Product story

This is the bridge from **where service is unreliable** to **where improvement would help
the most people**. It should be introduced only after core delay measurements are trusted.

## Related reports

- [Data report index](README.md)
