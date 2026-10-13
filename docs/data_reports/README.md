# Data Reports

One report per data source. Each answers two questions:

1. **Engineering:** what the source contains, how it is keyed and timed, what to check,
   and what tables it feeds.
2. **Product:** what story it tells a planner and what we must not claim.

## Index

| Report | Status | Phase | Covers |
|---|---|---|---|
| [KCM realtime](kcm_realtime.md) | Collecting | 1-2 | Vehicles, trip predictions, alerts |
| [Static GTFS](static_gtfs.md) | Planned | 3 | Routes, stops, schedules, shapes, calendars |
| [NOAA weather](noaa_weather.md) | Planned | 6 | Weather observations |
| [WSDOT traffic](wsdot_traffic.md) | Planned | 6 | Traffic flow, travel times, incidents |
| [Seattle Fire 911](seattle_fire_911.md) | Planned | 6 | Dispatch incidents |
| [Ridership](ridership.md) | Planned | 6 | Boardings and rider weighting |
| [Events](events.md) | Planned | 6 | Games, concerts, event windows |
| [Template](_template.md) | Template | | Structure for a new report |

Related docs: [KCM background](../kcm_background.md), [collection pipeline](../collection_pipeline.md) (how data reaches
Databricks), [free-tier limits](../limits.md), and [decisions](../decisions/).

## Conventions

- **Header table:** every report starts with the same table (status, phase, owner,
  format, cadence, profiled date, target tables). Keep it current; the index above
  must match.
- **Status values:**
  - **Planned:** named in the design, not yet collected or profiled.
  - **Collecting:** data is being collected and has been profiled at least once.
  - **Observed:** profiled from real data, with measured numbers. Use this once a
    planned source has been profiled but is not yet collected continuously.
  - **Unverified:** an assumption that needs a check before publication.
- **Section names:** use the template's names so reports can be compared. Planned
  sources keep sections short; add the optional sections once real data exists.
- **Facts versus hypotheses:** measured numbers state their date and where they came
  from. Mark anything unmeasured as such.
- **Examples:** sanitized, never containing credentials.

## Adding or updating a report

1. Copy [_template.md](_template.md) to `<source>.md` (lowercase, underscores).
2. Fill the header table and add a row to the index above.
3. When data exists, add measured counts, null rates, key behavior, and time coverage,
   and move the status forward.
4. Note source terms, attribution, and privacy limits before publishing results.
5. Record storage and volume implications in [limits.md](../limits.md) if the source is
   large.
