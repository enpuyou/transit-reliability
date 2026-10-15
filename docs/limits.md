# Free-tier limits and our usage

Checked 2026-10-13 against the official pages listed at the bottom. Re-check when
something starts failing, because free-plan limits change.

## Status summary

| Area | Limit | Our usage | Status |
|---|---|---|---|
| Cloudflare cron triggers | 5 per account | 1 | OK |
| Cloudflare requests | 100,000 per day | about 1,440 cron runs per day | OK |
| Cloudflare CPU per cron run | 10 ms | 6 to 10 ms over 11 runs after the 20-second change (was 4 to 5 ms); one run hit 10 ms | AT RISK, see decision 0003 |
| Cloudflare subrequests | 50 per invocation | about 10 (fetch + upload per source, vehicle positions 3x) | OK |
| Cloudflare memory | 128 MB | largest body is 1.2 MB | OK |
| Cloudflare log events | 200,000 per day, 3 day retention | about 10,000 per day | OK |
| Cloudflare log size | 256 KB per request | under 1 KB | OK |
| Databricks concurrent job tasks | 5 | 1 | OK |
| Databricks SQL warehouse | 1, 2X-Small | used only for manual checks | OK |
| Databricks compute quota | not published; overrun stops compute for the rest of the day, in extreme cases the month | not measured yet (see below) | UNKNOWN, must measure |
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
- **Cloudflare CPU is the tightest verified limit.** Measured 6 to 10 ms per run against a
  10 ms limit since vehicle positions moved to three fetches per run (4 to 5 ms before). A slower upload day or larger feed could exceed it. Runs at 10 to 13 ms
  were still reported `ok`, so enforcement is not strict per run, but a run that is
  terminated would lose that minute. The paid plan is not an option (project decision).
  Mitigations: watch the tail; try the CPU reductions listed in decision 0003; as a last
  resort, fetch trip updates less often.

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
