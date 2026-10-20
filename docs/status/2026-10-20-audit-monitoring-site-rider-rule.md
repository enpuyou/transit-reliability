# Project Status: audit, monitoring, public site, rider-rule headline

Date: 2026-10-20 (data from 2026-10-13 21:43 UTC, so 7 days collected)
Branch: `main`, pushed through commit 60ea2c3 (plus the docs added after it).

## Where the phases stand (design doc section 8)

| Phase | State |
|---|---|
| 1 Collector | Live. A second Worker (`transit-dispatcher`) only starts GitHub workflows on time (decision 0020). |
| 2 Bronze | Done, prod ingest every 30 minutes. |
| 3 Silver | Done, except `alert_versions` (alert feeds not collected richly yet). |
| 4 Public site | Changed: a transparency site on GitHub Pages instead of the planned live map (decision 0018). Live map open (decision 0013). |
| 5 Arrivals and metrics | Done except `docs/metrics.md` (definitions live in `docs/gold_tables.md` and the decisions): stop visits, trip and route reliability, prediction accuracy, rider-rule headline (0021), segment running times (0022), headways and bunching (0023). Ride test waived. |
| 6 Context and "why / who" | Not started. Gate G2 checked 2026-10-20: no licensed route- or stop-level ridership source found, so rider-minutes-lost is dropped as the headline (0023). Weather, events, WSDOT, 911 open. |
| 7 ML | Not started; needs 3 weeks of data, so not before about 2026-11-03 (gate G4). |
| 8 Hardening | About half: CI, checks as code, runbook, monitoring, ADRs. Open: auto-deploy, smoke test, outage drill, README reproduction pass. |
| 9 Polish | Not started. |

## Decisions since the last status note

0016 independent audit (stale-pointer bug), 0017 monitoring, 0018 transparency site, 0019 GPS wins over the
next-stop pointer (zero-width arrivals 9.5 to 0.01 percent), 0020 Cloudflare alarm clock for GitHub
workflows, 0021 rider rule as the headline (last stop left out). Dropped on 2026-10-20: the planned
off-Databricks backup of silver snapshots (design doc risk table, Cloudflare R2): the owner accepts that a
deleted workspace would lose raw files and silver tables, which cannot be re-fetched.

## What runs (prod)

- Jobs: ingest every 30 min, monitor hourly (about 15 DBU a day, six times the first estimate; slowing it
  is proposed), silver daily 11:45 UTC; all email on failure. GitHub: probes every 15 min and a watchdog
  hourly (both started by the dispatcher), the daily GTFS download.
- Checks: 15 hourly, 32 daily (audit plus drift plus rider coverage); 27 passed, 0 failed, 3 warnings on
  2026-10-18.
- Public site: https://enpuyou.github.io/transit-reliability/ (Seattle time, interactive charts,
  freshness panel that separates pipeline problems from a stalled page).

## Measured (service dates 2026-10-14 to 10-18)

- Headline (rider rule), early / on time / late / uncertain: 10-16 6.9 / 69.6 / 16.6 / 6.9 percent
  (68,808 timepoints scored, 94 percent of non-last timepoints). Arrival-only view: 12.5 / 64.2 / 16.3 / 7.1.
- Agency predictions vs persistence, median absolute error at 5 / 15 / 30 min: 62 / 96 / 132 s vs
  57 / 94 / 132 s; schedule 144 / 159 / 177 s.
- Compute: 21 to 40 DBU a day so far; quota unpublished, no cut-off seen.

## Open items and next

1. Phase 6 context data (weather first), or the Phase 8 hardening items.
2. Slow the hourly monitor (about 10 DBU a day saved); reword or retire the two arrival-coverage warnings.
3. Decision 0013 (live view); gate G2 ridership spike; `alert_versions`.
4. Known unknowns: why scheduled runs vanished on 2026-10-19 17:30 to 22:30 UTC (cause unverified);
   failure-email delivery never observed.
