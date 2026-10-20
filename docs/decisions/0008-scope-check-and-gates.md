# 0008: Scope check: an independent reliability audit, with go/no-go gates

Date: 2026-10-15
Status: accepted

## Context

After Phase 2 and the first silver tables, the data has shown what it can and cannot
support (`docs/silver_tables.md`, section 3b), measured 2026-10-14/15:

- About 48 percent of trips are first seen after their scheduled departure; 4 percent are
  never seen. On-time **departure** is directly measurable for only about half of trips.
- `current_stop_sequence` is not literal (a bus was reported stopped at sequence 2 before
  the trip's first stop was due), so arrivals must be inferred from position along the route.
- 55 percent of trip-update snapshots on one route-day were pure schedule (every delay zero);
  early "predictions" are not predictions.
- We observe positions about every 20 s and predictions about every 60 s.
- KCM's prediction method is undocumented. One 2019-2020 student poster describes a linear
  distance-and-speed model; our data does not settle it.
- Only one provider (KCM, two formats) is integrated today. Weather, traffic, events, 911,
  and ridership are planned and unbuilt.

The design doc's headline promised to tell planners where unreliability costs riders most,
why, and what to change. The evidence supports the first and the benchmark part well, and the
"why" and "who" parts weakly.

## Decision

1. **Reframe the headline:** an independent, reproducible audit of King County Metro's
   schedule adherence and prediction accuracy, built from public feeds, with explicit
   uncertainty. "Recommendations to planners" becomes a stretch outcome, not the promise.
2. **Confidence tiers** (what we claim):
   - High: a reproducible history of the feeds joined to the schedule with quality rules and
     measured coverage; trip delivery; running time vs schedule; KCM prediction error vs
     actual arrival, by horizon.
   - Medium: our own delay model beating the schedule and persistence baselines.
   - Low (do not headline): causal "why"; rider-weighted impact without real ridership;
     any claim that KCM would change service.
3. **Gates** (a gate that fails changes the plan; it does not get argued away):
   - **G1, arrival validation (end of Phase 5):** originally a ride test of 5 or more stops
     (median absolute error under 60 s, coverage above 85 percent of scheduled stop visits).
     **Revised 2026-10-16 at the owner's request: the ride test is optional, not required.**
     Instead the error budget is measured from the data (sampling interval width; sensitivity of
     the arrival rule; where dwelling buses sit relative to the stop point; see decision 0011) and
     the claim is limited accordingly: times are *estimates from GPS with an interval*, never
     presented as ground truth. Coverage stays a reported number. A ride test would remove the one
     unmeasurable term (GPS clock offset, door opening versus reaching the stop point) and remains
     welcome. If an independent source of true times appears, use it.
   - **G2, ridership spike (before Phase 6):** confirm a ridership source exists with route or
     stop granularity and a usable license. If not, drop rider-minutes lost as the headline
     and use delay and trips affected.
   - **G3, publication check (before anything is public):** read KCM's terms of use for the
     realtime feeds, record the required attribution, and look at what Metro already
     publishes so claims of novelty are accurate. Status 2026-10-15: terms read and recorded
     in `docs/data_reports/kcm_realtime.md` (revocable license to use and redistribute,
     attribution legend required, data as-is). The check of what Metro already publishes was done on
     2026-10-19 (web search, secondary sources; confirm on kingcounty.gov before quoting): Metro
     publishes a rider dashboard ("The Dash"), a Strategic Plan dashboard and performance reports, and
     is reported to define on time as at most 5.5 minutes late or 1.5 minutes early (our `metro_like`
     lens). So nothing here may be presented as the first measure of Metro's reliability. The
     transparency site (decision 0018) is public from that date with the attribution legend and
     aggregates only; it presents the pipeline and its checks, not a ranking of Metro.
   - **G4, enough history (before Phase 7):** at least 3 weeks of collection and enough
     stop visits per horizon for stable error estimates. Otherwise postpone modeling.
   - **G5, compute headroom:** daily DBU stays inside a level we choose and record in
     `docs/runbook.md` (quota unknown); if exceeded, lengthen the ingest interval first.
4. **Integration order for Phase 6** by value per effort: ridership, weather, WSDOT traffic;
   defer events and 911 until G1 passes.
5. **An interactive tool is a goal, but it comes later.** The owner decided on 2026-10-15
   that visualization is too early, and that it should eventually be an application
   **connected to the Databricks instance**, not a viewer of exported static files. It should
   be built on the validated metric tables (stop visits, delay, prediction error), so that
   what it shows is something we have checked. Not decided yet: how it connects (for example
   an owner-only app querying through the SQL warehouse, or a Databricks App) and who can use
   it. This matters because of the rule that the warehouse is never queried from a public
   serving path (CLAUDE.md) and the Free Edition limits (one 2X-Small warehouse, unpublished
   quota, apps that stop after 24 hours and require a workspace login): a private tool for
   the owner is consistent with the rule; a public one is not, and would need a different
   design. Decide this when the tool is designed. A throwaway static prototype was built and
   deleted on that date.

## Alternatives considered

- Keep the original headline: rejected; two of its legs (why, who) rest on data we do not
  have and cannot validate yet.
- Drop modeling entirely: rejected; the honest benchmark against the agency is valuable even
  when the result is "we do not beat it", and the data will exist by Phase 7.

## Consequences

- The design doc's later phases are re-ordered by gate, not by calendar.
- Findings must carry coverage, sample size, and an interval. A metric without those does not
  go on the site.
- Revisit this decision at G1 and G2; record the outcome in a status note.
