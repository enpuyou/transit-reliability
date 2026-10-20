# 0020: A Cloudflare Worker as the alarm clock for GitHub workflows

Date: 2026-10-20
Status: accepted and verified (deployed 2026-10-20; dispatched runs arrived at 19:30:26 and 19:45:26 UTC, 26 seconds after the quarter hour, and completed)

## Context

The site's live probes (`publish_site.yml`, every 15 minutes) and the alerting watchdog (`watchdog.yml`,
hourly) were scheduled with GitHub's `schedule` trigger. Measured on 2026-10-20 (`gh run list --event
schedule`): `publish_site` started 2 scheduled runs in about 17 hours (about 64 slots), 4 to 9 minutes
late; `watchdog` 3 of about 17, 10 to 20 minutes late; the daily GTFS download ran 177 to 374 minutes
late on every one of its 5 days. GitHub documents this: the `schedule` event "can be delayed during
periods of high loads" and "some queued jobs may be dropped"; the shortest interval is 5 minutes; it
runs on the default branch; and scheduled workflows are disabled after 60 days without repository
activity. Why this repository is dropped so often is not observable from outside (unverified: low
activity or a frequent cron may be deprioritised).

The consequence was visible: the page said "Cannot confirm the pipeline is alive" while the pipeline
was fine, and the "GitHub emails you" safety net was much weaker than claimed.

## Decision

- The jobs stay in GitHub Actions. A new Worker `workers/dispatcher` (own project, own cron, so it can
  never affect the collector's 10 ms CPU budget) only sends one request: the GitHub REST
  `workflow_dispatch` call. Cloudflare crons fire on time (the collector depends on this every minute)
  and a dispatched run starts immediately.
- Crons: `*/15 * * * *` dispatches `publish_site.yml`, `5 * * * *` dispatches `watchdog.yml`.
- The workflows keep their own `schedule` entries as a fallback. Both have `workflow_dispatch` and a
  `concurrency` group, so a dispatch and a late scheduled run never overlap.
- Credential: a fine-grained GitHub token limited to this one repository with only "Actions: read and
  write", stored as the Worker secret `GITHUB_TOKEN` (never in the repo; never logged). It expires (at
  most one year): the page then goes red ("Cannot confirm...") because probes stop, which is the signal.
- Failed dispatches throw, so the invocation shows as failed in the Cloudflare dashboard.

## Also fixed (found while investigating)

- Hourly check `scheduled_trips_tomorrow` failed every night from 00:00 to 04:45 Seattle time (the daily
  job expands the schedule at 04:45; "tomorrow in Seattle" was not expanded yet). Replaced by
  `schedule_expanded_days_ahead_of_today` (minimum 0), which is always true while the daily job runs.
- The monitor job published the site even when a check failed (`run_if: ALL_DONE`), so the run ended
  `SUCCESS_WITH_FAILURES`, which may not send the failure email (unverified). A task
  `fail_if_checks_failed` (`run_if: AT_LEAST_ONE_FAILED`) now fails the run after publishing, so the
  email fires and the page still shows the failure. The daily job no longer uses `ALL_DONE`.

## Verify after deploying

The collector's lesson applies (a new Worker's cron did not fire on 2026-10-13): after `npm run deploy`
confirm that `gh run list --workflow publish_site.yml --event workflow_dispatch` shows a run at each
quarter hour before relying on it.

## Deployment notes (2026-10-20)

- First attempt put the token on the WRONG Worker: `wrangler secret put` picks the Worker from the
  `wrangler.toml` of the current folder, so it landed on `transit-collector`. Found with
  `wrangler secret list --name <worker>` (names only; secret values cannot be read back), removed from
  the collector, set on the dispatcher. Always pass `--name transit-dispatcher` (commands.md).
- A first commit accidentally included 295 MB of `node_modules` (the ignore rule covered only the
  collector's folder); undone before pushing and `node_modules/` is now ignored repository-wide.
