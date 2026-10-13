# Data Reports

This directory is the data catalog for `transit-reliability`. Each report answers two
questions:

1. **Engineering:** What does the source physically contain, how is it keyed and timed,
   what quality checks are needed, and what tables or features can we build?
2. **Product and planning:** What story does the source tell, who would use it, what
   decisions could it support, and what should we avoid claiming?

Every source report should include a real example from the repository or a clearly marked
representative example. Examples should be sanitized and should never contain credentials.

## Reports

| Report | Status | What it covers |
|---|---|---|
| [KCM realtime](kcm_realtime.md) | Available | Live vehicles, trip predictions, stop timing, and alerts |
| [Static GTFS](static_gtfs.md) | Planned | Routes, stops, schedules, shapes, service calendars, and modes |
| [NOAA weather](noaa_weather.md) | Planned | Weather observations and delay context |
| [WSDOT traffic](wsdot_traffic.md) | Planned | Traffic flow, travel times, and highway incidents |
| [Seattle Fire 911](seattle_fire_911.md) | Planned | Dispatch incidents and possible route impacts |
| [Ridership](ridership.md) | Planned | Boardings, rider weighting, and rider-minutes lost |
| [Events](events.md) | Planned | Games, concerts, attendance windows, and event-day context |
| [Report template](_template.md) | Template | Required structure for adding a new source |

The existing broad inventory is retained in [data_inventory.md](../data_inventory.md). It
summarizes the overall system; the reports here go deeper source by source.

## How to read a report

- **Source role:** why the data exists and how often it changes.
- **Shape:** what one record means and how records relate to each other.
- **Example:** what an actual row or message looks like.
- **Available now:** analysis possible from the source alone.
- **Requires joins:** analysis that needs static GTFS, historical snapshots, or another source.
- **Quality and caveats:** what can make an interpretation wrong.
- **Product story:** how to explain the value to a planner or stakeholder.

## Data maturity labels

- **Observed:** verified from a checked-in fixture or completed collection.
- **Planned:** named in the design but not yet collected or profiled.
- **Derived:** calculated by this project from one or more sources.
- **Unverified:** an assumption that needs a manual or empirical check before publication.

## Report maintenance

When adding a source:

1. Copy [_template.md](_template.md) into a new report.
2. Add the source to the table above.
3. Include at least one example record and its capture date.
4. Record field counts, null rates, distinct identifiers, and time coverage when data exists.
5. Explain the engineering outputs and the stakeholder story separately.
6. Link the report from `README.md` if it becomes part of the main workflow.
7. Note source terms, attribution, and privacy constraints before publishing results.
