# 0023: Headways, bunching and gaps

Date: 2026-10-20
Status: accepted and built

## Decision

- Headway = the gap between two consecutive buses of a route and direction leaving the same timepoint,
  measured on the DEPARTURE estimate (the rider's wait is for the bus to leave). Pairs follow the SCHEDULED
  order of trips; a negative actual gap (the buses swapped order) is bunching.
- Bunching is only meaningful on frequent service, so classes are reported for pairs scheduled 4 to 15
  minutes apart. Thresholds from the design doc: bunched below 25 percent of the scheduled gap; gapped
  above 200 percent (our addition; the design doc names only bunching).
- Classification follows the project rule for estimates: a pair is bunched, gapped or regular only when the
  WHOLE interval of the actual gap says so (`classify_headway`, pure and tested, with an equal SQL twin);
  otherwise uncertain. The interval comes from the two departure intervals and is about 50 s wide, which
  is why short scheduled gaps (4 to 5 minutes) produce more uncertain pairs.
- Tables `headway_pairs` (per pair, about 62,000 a day) and `headway_summary` (28-day counts by route,
  direction, band, day type). Public page: aggregate shares by time of day only; route-level results stay
  in the tables and findings (as for segment running times).
- Verified in a sandbox on 2026-10-16: audit recount equal (row-number join versus lag()), Python reference
  equals the SQL classes on all 62,691 pairs.

## Gate G2 (ridership) outcome, recorded here because it decides Phase 6

Checked on 2026-10-20 against primary sources: the King County open data catalog has no downloadable
route- or stop-level ridership dataset for Metro (its only Metro item is a link to "The Dash" rider
dashboard); Metro's data and reports page lists dashboards and a reports search but no ridership files, file
formats, or terms. Route-level boardings quoted by Seattle Transit Blog are secondhand with no stated source
or license. So no machine-readable, licensed ridership source is verified. Per decision 0008 (G2): drop
rider-minutes-lost as the headline and keep delay and trips affected. Possible later routes (unverified):
the annual Service Guidelines report tables, or asking Metro or a public records request.
