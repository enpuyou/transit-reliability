# 0017: Monitoring, and how the owner inspects it

Date: 2026-10-19
Status: accepted

## Context

Job status is not evidence (the 2026-10-16 watermark bug). On 2026-10-19 eleven scheduled ingest runs
never started: no failed run, so no email. The owner cannot review all the data and the code was
written by an agent, so the pipeline must prove itself and must tell the owner when it cannot.

## Decision

Four layers, each with a different way of reaching the owner:

1. **Hourly monitor job** (`transit-reliability-prod-monitor`, :12 past the hour): freshness of each
   bronze table and of the newest collector snapshot, snapshots per full hour, longest gap, silver and
   gold currency, schedule expanded for today and tomorrow, days of schedule left. A failing check
   fails the job, which emails the owner.
2. **Daily checks** (last task of the silver job): the independent audit checks (`audit/catalog.py`)
   plus drift against the previous three days. Known limits are severity `warn`: recorded and shown,
   never failing, so a new failure stays visible.
3. **Results table** `transit_ops.check_results`: every result with value, rule, status, and the SQL
   to rerun it. Written before the task fails.
4. **External watchdog** (GitHub Actions, hourly, `scripts/watchdog.py`): outside Databricks because
   a job that never starts cannot report itself. Checks that the collector landed a file in the last
   10 minutes, that the ingest and monitor jobs started recently and their latest finished run
   succeeded, and that the daily job ran today once due. Red run = GitHub email.

And a **proof report** (`scripts/proof_report.py`) for inspection by eye: the newest result of every
check, plus five random trips with the raw GPS fixes either side of each claimed arrival.

## Consequences

- Industry practice is alerts for noticing and dashboards for diagnosis; no dashboard is built yet.
- The monitor adds about 24 short serverless runs a day; measure and record in `docs/limits.md`.
- GitHub emails only on failure; "all green" is read on the Actions page. Scheduled workflows are
  disabled after 60 days without repository activity.
- The watchdog uses the same Databricks token as the GTFS workflow; if it expires, the watchdog goes
  red ("could not check"), which is the intended behaviour.
- Warn-level checks do not alert. Review them when reading the proof report.
