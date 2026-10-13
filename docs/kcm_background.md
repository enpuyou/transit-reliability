# King County Metro: background and where to find things

Checked 2026-10-13. Facts from third parties are labeled; verify before publishing.

## Who they are

King County Metro (KCM) is the public transit agency for King County, Washington
(Seattle and surroundings). Per its own site it operates bus, on-demand, paratransit,
vanpool, and water taxi service, and also operates the Seattle Streetcar, Sound Transit
Link light rail, and Sound Transit Express buses. The King County Council adopts its
budget (2026-2027: $4 billion operating and capital, adding more than 400,000 service
hours), and a Transit Advisory Commission advises the agency.

Scale, from outside sources (not the agency page; confirm before citing):

- Wikipedia: about 94.5 million trips in 2025, 132 routes, 1,089 buses.
- Federal National Transit Database 2024 profile: 88.6 million annual trips, service area
  2,134 square miles, population served 2.35 million.
- Metro's own blog (Oct 2025): about 63 million boardings through Aug 31, 2025, up 4.5
  million on the same period a year earlier.

It is the largest transit agency in the northwestern US, so reliability work here affects
a lot of riders. This is the reason a planner audience makes sense.

## Where the data is

| What | Where |
|---|---|
| Developer page (start here) | https://kingcounty.gov/en/dept/metro/rider-tools/mobile-and-web-apps |
| Static GTFS (schedule) | https://metro.kingcounty.gov/GTFS/google_transit.zip (about 10.9 MB, verified 200) |
| Realtime feeds | https://s3.amazonaws.com/kcm-alerts-realtime-prod/ |
| Agency data and reports | https://kingcounty.gov/en/dept/metro/about/data-and-reports |
| County open data portal | https://data.kingcounty.gov (catalog entry "King County Metro - Developer Resources") |
| Strategic plan, service guidelines, Metro Connects (30-year vision) | PDFs linked from https://kingcounty.gov/en/dept/metro/about |
| General contact | https://contact.kingcounty.gov/ (the developer page lists no dedicated developer contact) |

Note: `design_doc.md` originally listed a Sound Transit URL for the static GTFS; that URL
now redirects. Use the Metro URL above.

### Realtime file variants

Each feed is published in three formats at the same base URL:

| Feed | Protobuf (we use) | JSON | Enhanced JSON |
|---|---|---|---|
| Vehicle positions | `vehiclepositions.pb` | `vehiclepositions_pb.json` | `vehiclepositions_enhanced.json` |
| Trip updates | `tripupdates.pb` | `tripupdates_pb.json` | `tripupdates_enhanced.json` |
| Alerts | `alerts.pb` | `alerts_pb.json` | `alerts_enhanced.json` |

Checked the enhanced vehicle JSON (493 KB versus 62 KB for the protobuf): it carries the
same fields plus **`block_id`**, the id of the block (a vehicle's chain of consecutive trips
for the day), with readable status names. Block id is valuable for layover, recovery time,
and "the late bus is the same bus that runs the next trip" analysis. The protobuf does not
include it. It is not parsed or stored by this project yet; consider adding the enhanced
JSON for vehicle positions (or at least sampling it) before Phase 5. The other enhanced
files were not inspected.

## Documentation

- The developer page describes the three feeds and states GTFS-Realtime "must be used in
  conjunction with" Metro's static GTFS. It links no field-level documentation and no
  update frequency. Anything about cadence in this repo is measured, not documented
  (`docs/collection_pipeline.md`).
- Standards (not KCM-specific): the GTFS and GTFS-Realtime specifications are maintained
  at https://gtfs.org (static reference and realtime reference). Field meanings in KCM's
  feeds follow those specs; agency-specific choices (which optional fields exist, whether
  ids are stable) have to be learned from the data.
- Agency-level context (service guidelines, performance reports) lives under the
  data-and-reports and about pages above. Reading the service guidelines is worth doing
  before defining "on time" or schedule-change recommendations, because the agency's own
  thresholds should anchor the metrics (the design doc already flags confirming Metro's
  official on-time window).

## Terms of use (must follow before publishing)

From the developer page and its Transit Data Terms of Use:

- A "limited, revocable license to use, reproduce, and redistribute the Data."
- **Attribution required:** "Transit scheduling, geographic, and real-time data provided
  by permission of King County."
- Data is provided "AS IS" and "AS AVAILABLE"; no warranty.
- King County may change the terms, or modify or discontinue access, at any time, with or
  without notice. (This is a reason raw files are kept and collection is decoupled.)
- Includes an indemnification clause; jurisdiction is the Western District of Washington
  or King County Superior Court.

Put the attribution line on the public site and in any shared report. Record this in
`docs/data_sources.md` when that file is created (design doc requirement).

## Things not known

- Whether the feed's cadence or format will change (the page says data "will change over
  time and without notice").
- Whether KCM publishes an archive of past realtime data. None was found.
- Whether there is any rate limit on the S3 feeds. None is documented; we poll three
  times a minute for vehicle positions and once for the other feeds.
