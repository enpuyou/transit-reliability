# Free-tier limits and our usage

Checked 2026-10-13 against the official pages listed at the bottom. Re-check when
something starts failing, because free-plan limits change.

## Status summary

| Area | Limit | Our usage | Status |
|---|---|---|---|
| Cloudflare cron triggers | 5 per account | 3 (collector 1, dispatcher 2) | OK |
| Cloudflare requests | 100,000 per day | about 1,440 cron runs per day; the live-map relay (once deployed) adds about 4,300 per day per map left open, and the agency is asked at most ~4 times a minute (unverified until deployed) | OK |
| Cloudflare CPU per cron run | 10 ms | 9 to 16 ms over 9 runs on 2026-10-15 (median 11; 16 on the minute that also fetched alerts); 6 to 10 ms on 2026-10-13. All 9 reported `ok`, no exceptions | AT RISK and over the stated limit without failing; enforcement is not strict per run. See decision 0003 |
| Cloudflare subrequests | 50 per invocation | about 10 (fetch + upload per source, vehicle positions 3x) | OK |
| Cloudflare memory | 128 MB | largest body is 1.2 MB | OK |
| Cloudflare log events | 200,000 per day, 3 day retention | about 10,000 per day | OK |
| Cloudflare log size | 256 KB per request | under 1 KB | OK |
| Databricks concurrent job tasks | 5 | 1 | OK |
| Databricks SQL warehouse | 1, 2X-Small | used only for manual checks | OK |
| Databricks compute quota | not published; overrun stops compute for the rest of the day, in extreme cases the month | 2026-10-15: 21.5 DBU jobs + 10.7 DBU SQL (a heavy day: 48 min rebuild and verification queries); no cut-off seen | UNKNOWN quota; measured usage in `docs/runbook.md` |
| Databricks storage | not published | about 1.4 GB per day raw measured (2.66 GB after 46 hours, 2026-10-15; earlier estimate 1.8); bronze about 0.29 GB total | UNKNOWN, must bound |
| Databricks outbound internet | trusted domains only | none needed (push design) | OK |
| Databricks inactivity | accounts may be deleted after a prolonged period, threshold not published | active | OK |

## What is not verified

- **Databricks daily compute quota has no published number.** The only evidence is that
  overrun shuts compute off for the day. So we cannot prove we are within it; we can only
  measure usage and keep it small. As of this check, `system.billing.usage` showed 0.004
  DBU for today, from the dev setup and ingest runs. System tables lag, so recheck after
  the prod schedule has run for a day.
- **Databricks storage has no published number.** Raw files alone grow about 1.8 GB per
  day (1.2 MB trip updates per minute plus 54 KB vehicle positions), roughly 54 GB per
  month, before bronze Delta tables. Do not assume this fits. Retention below is the
  mitigation.
- **Cloudflare CPU is the tightest verified limit.** Measured 6 to 10 ms per run on 2026-10-13 and 9 to 16 ms on 2026-10-15 (9 runs, all `ok`), against a
  10 ms limit since vehicle positions moved to three fetches per run (4 to 5 ms before). A slower upload day or larger feed could exceed it. Runs at 10 to 13 ms
  were still reported `ok`, so enforcement is not strict per run, but a run that is
  terminated would lose that minute. The paid plan is not an option (project decision).
  Mitigations: watch the tail; try the CPU reductions listed in decision 0003; as a last
  resort, fetch trip updates less often.

## Cloudflare CPU limit: what the docs say and how we measure

Source: https://developers.cloudflare.com/workers/platform/limits/ (read 2026-10-15). The
Workers Free plan lists **10 ms CPU time per Cron Trigger**, and scheduled handlers are
covered. Enforcement wording, quoted: "Each isolate has some built-in flexibility to allow
for cases where your Worker infrequently runs over the configured limit." and "If a Worker
consistently hits the limit, its execution will be terminated according to the limit
configured." A terminated run shows in the dashboard as "Exceeded CPU Time Limits" and in
analytics and Logpush as outcome `exceededCpu`. Cron Triggers have a 15 minute wall-time
maximum.

We measure CPU from `wrangler tail --format json`: each invocation event carries a `cpuTime`
(whole milliseconds) and `wallTime` reported by Cloudflare itself. We do not time it
ourselves. Our median of 11 ms is "consistently" over the limit, so the unexplained
5-minute outages (2026-10-14 02:45 UTC, 2026-10-15 17:01 UTC) could be terminations, but
that is a hypothesis: confirm by looking for `exceededCpu` outcomes in the Cloudflare
dashboard logs for those windows.

## Operating rules to stay within limits

1. **Ingest schedule:** 30 minutes. Do not go lower without measuring first.
2. **Raw retention:** once silver is validated, delete raw trip-update files older than
   30 days (policy and read-only report: decision 0005). Keep vehicle positions and alerts longer (they are small). Never delete
   raw before the silver tables built from it are verified.
3. **Do not read from the warehouse in any public path.** Exports are files.
4. **Keep job DAGs narrow:** at most 5 concurrent tasks.
5. **Measure after every phase:** run the usage queries in `docs/commands.md` and record
   the numbers in `docs/runbook.md`.
6. **Stop condition:** if Databricks usage shows more than 50 percent of an unknown
   quota's effect (any day compute is cut off), pause the ingest schedule and reduce
   frequency before investigating. Collection keeps running; files accumulate safely.

## Sources

- Databricks Free Edition limitations: https://docs.databricks.com/aws/en/getting-started/free-edition-limitations
- Cloudflare Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- Cloudflare Workers Logs: https://developers.cloudflare.com/workers/observability/logs/workers-logs/

## Monitoring compute (added 2026-10-19; decision 0017)

- Measured runtimes 2026-10-19/20: hourly suite 99 s (15 checks), daily suite 105 s (32 checks), both
  serverless job runs. The daily suite runs inside the silver job (adds about 105 s to its 588 s).
- ESTIMATED cost, not yet billed data (system.billing.usage lags about a day): ingest runs of about
  4 to 7 min cost about 0.34 DBU each on 2026-10-19 (12.0 DBU over 70 usage rows), so one 99 s monitor
  run is about 0.1 DBU, 24 runs a day about 2.5 DBU, plus about 0.2 DBU for the daily suite. Against
  the 21 to 26 DBU/day of jobs compute seen 2026-10-16 to 18 that is roughly +10 percent. Quota is
  unpublished, so no headroom can be stated.
- Re-check once a full day has billed (first full day: 2026-10-21): 
  `SELECT usage_date, usage_metadata.job_id, round(sum(usage_quantity),2) AS dbu FROM system.billing.usage WHERE usage_date >= '2026-10-20' AND usage_metadata.job_id IS NOT NULL GROUP BY ALL ORDER BY 1, 3 DESC`
  The monitor job id is 997733005982178. If it exceeds about 4 DBU/day, change its cron to every
  2 hours (`0 12 0/2 * * ?`) in `resources/monitor_job.yml` and redeploy.
- The watchdog and the proof report use no Databricks compute (jobs API and file listing only) except
  the SQL warehouse for the proof report.

## Transparency site (added 2026-10-20; decision 0018)

- The `publish_site` tasks add compute to the monitor job (live sections, expected well under a
  minute: the sandbox timings of the live queries were 5 to 12 s each) and to the silver job (the heavy
  sections, 5 to 11 s each in the sandbox). NOT yet measured in production; record the first full
  day's job DBU here (query in "Monitoring compute" above) and compare.
- `site.json` was 223 KB in the first preview (14 tables, 7 days of hourly data). GitHub Pages limits
  are far above that (unverified for this repository's plan).

## Measured compute by job (system.billing.usage, 2026-10-13 to 10-20; corrects the 2026-10-19 estimate)

DBU per UTC day (billing lags about a day; 10-20 is billed only through 17:00 UTC):

| Day | ingest | silver | monitor | dev and one-off runs | SQL warehouse | other | total |
|---|---|---|---|---|---|---|---|
| 10-15 | 18.74 | 1.88 | 0 | 3.94 | 14.34 | 1.41 | 40.31 (highest day, no cut-off) |
| 10-16 | 16.84 | 6.50 | 0 | 1.18 | 8.81 | 1.41 | 34.74 |
| 10-17 | 18.43 | 1.93 | 0 | 0 | 0 | 0.65 | 21.01 |
| 10-18 | 16.59 | 2.09 | 0 | 2.79 | 2.83 | 0.41 | 24.71 |
| 10-19 | 13.23 | 2.25 | 0 | 1.97 | 3.56 | 1.19 | 22.20 |
| 10-20 (to 17:00) | 11.49 | 2.86 | 10.53 | 4.17 | 5.80 | 1.28 | 36.13 |

The monitor job cost 10.53 DBU over its 17 runs that day, about 0.62 DBU per run (two tasks, each with
its own serverless start; about 126 s), so about 15 DBU per day at 24 runs. The 2026-10-19 estimate in
"Monitoring compute" above (about 2.5 DBU per day) was wrong by a factor of six: it scaled the per-minute
cost of ingest runs, but short two-task runs cost far more per minute. At 15 DBU per day the monitor is now
the largest single consumer after ingest. The quota itself is still unpublished, so no headroom can be
stated; the highest day so far (40.3 DBU) did not hit a cut-off, but 10-20 is on course to exceed it.
