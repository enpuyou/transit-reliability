# Outage drills: break something on purpose, see that we notice and can fix it

A monitoring system is only proven when it catches a real failure. Each drill below breaks one piece in a safe,
reversible way, checks which alarm fires and how fast, and follows the docs to recover. Record the date and what
happened at the bottom; anything that did not alert is a gap to fix.

## Drill 1: the relay stops answering (safe: simulated, nothing in production changes)

```bash
uv run python - <<'PY'
import datetime as dt
from transit_reliability.monitoring.watchdog import relay_memory_finding, relay_vehicles_finding
print(relay_vehicles_finding(502, 0))                       # what the watchdog reports when the relay fails
print(relay_memory_finding({"lastRun": dt.datetime.now(dt.UTC).timestamp() - 900}, dt.datetime.now(dt.UTC)))  # memory asleep
PY
```

Expected: both FAIL. In production the hourly watchdog would then fail its GitHub run, and GitHub emails the owner.
Recovery: `cd workers/relay && npx wrangler deploy` (redeploys); `curl .../memory/status` wakes the memory.

## Drill 2: the weather fetch stops (real: pause one workflow for 5 hours)

`gh workflow disable weather.yml`, wait 5 hours, look at the hourly monitor run (`weather_minutes_since_last_fetch`
should fail after 4 hours and the monitor job email you), then `gh workflow enable weather.yml` and run it once:
`gh workflow run weather.yml -f hours=12`. The 12-hour fetch fills the gap (the weather service keeps 7 days).

## Drill 3: a deploy that is missing a step (safe: compare, do not deploy)

Add a fake step to a resource file locally, run `uv run python scripts/smoke_test.py --profile enpu`, and see the
"missing" line; revert the file. This proves the smoke test catches a deploy that does not match the repository.

## Drill 4: the daily job fails (happened for real on 2026-10-22)

The daily check `weather_observation_duplicate_keys` failed; the daily job emailed; the website refresh and map
history were skipped; the watchdog reported it ("ended FAILED"); the fix and the rerun are in
`docs/databricks_guide.md` section 8. Lesson recorded there.

## Log

| Date | Drill | Alarm that fired | Time to notice | Notes |
|---|---|---|---|---|
| 2026-10-22 | 4 (real) | daily job email; watchdog | same morning | watchdog first showed "no finished run yet" (fixed: INTERNAL_ERROR now counts as finished) |
| 2026-10-22 | 1 (simulated) | watchdog findings FAIL | immediate | see below |
| 2026-10-22 | 3 (simulated) | smoke test "missing" line | immediate | the 4 new silver steps were not yet deployed: the smoke test listed them |
