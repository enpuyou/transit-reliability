"""Pure rules of the external watchdog (stdlib only, so a GitHub runner can import it directly).

The watchdog lives OUTSIDE Databricks on purpose: a job that never starts leaves no failed run and
sends no email (2026-10-19: eleven ingest runs were silently missing). It asks Databricks what has
happened and judges the answers here.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

MAX_LANDING_AGE_MIN = {"vehicle_positions": 10.0, "trip_updates": 10.0, "alerts": 15.0}
MAX_INGEST_RUN_AGE_MIN = 75.0  # runs start every 30 min
MAX_MONITOR_RUN_AGE_MIN = 90.0  # runs start every 60 min
SILVER_DUE_UTC = dt.time(13, 30)  # the daily silver job starts 11:45 UTC and takes about 10 min
_DONE = "TERMINATED"
PACIFIC = ZoneInfo("America/Los_Angeles")
SILVER_STARTS_UTC = dt.time(11, 45)  # the daily job's cron


def pacific(moment: dt.datetime) -> str:
    """Wall-clock time in Seattle with the zone that applies on that date (PDT or PST)."""

    return moment.astimezone(PACIFIC).strftime("%H:%M %Z")


@dataclass(frozen=True)
class Finding:
    name: str
    ok: bool
    detail: str


def newest_epoch_ms(file_names: Sequence[str]) -> int | None:
    """Largest epoch-millisecond in names like `1792449250117.pb`; other names are ignored."""

    stamps = [int(n[:-3]) for n in file_names if n.endswith(".pb") and n[:-3].isdigit()]
    return max(stamps) if stamps else None


def landing_finding(source: str, file_names: Sequence[str], now: dt.datetime) -> Finding:
    newest = newest_epoch_ms(file_names)
    name = f"landing_{source}_newest_file"
    if newest is None:
        return Finding(name, False, "no collector files found")
    age = (now - dt.datetime.fromtimestamp(newest / 1000, dt.UTC)).total_seconds() / 60.0
    limit = MAX_LANDING_AGE_MIN[source]
    return Finding(name, age <= limit, f"newest file is {age:.1f} min old (limit {limit:g})")


def _started(run: Mapping[str, Any]) -> dt.datetime:
    return dt.datetime.fromtimestamp(run["start_time"] / 1000, dt.UTC)


def job_finding(
    name: str, runs: Sequence[Mapping[str, Any]], now: dt.datetime, max_age_min: float
) -> Finding:
    """`runs` newest first, as `databricks jobs list-runs` gives them.

    Fails when no run started recently (the job did not fire) or when the newest finished run did
    not succeed. A run still in progress is judged by the one before it.
    """

    if not runs:
        return Finding(name, False, "no runs found")
    age = (now - _started(runs[0])).total_seconds() / 60.0
    if age > max_age_min:
        return Finding(name, False, f"newest run started {age:.0f} min ago (limit {max_age_min:g})")
    finished = [r for r in runs if r["state"].get("life_cycle_state") == _DONE]
    if not finished:
        return Finding(name, False, "no finished run yet")
    result = finished[0]["state"].get("result_state")
    when = pacific(_started(finished[0]))
    if result != "SUCCESS":
        return Finding(name, False, f"latest finished run ({when}) ended {result}")
    detail = f"latest finished run ({when}) SUCCESS; newest started {age:.0f} min ago"
    return Finding(name, True, detail)


def silver_finding(runs: Sequence[Mapping[str, Any]], now: dt.datetime) -> Finding:
    """The daily job must have started today (UTC) and succeeded, once it is due."""

    name = "silver_job_daily_run"
    if now.time() < SILVER_DUE_UTC:
        return job_finding(name, runs, now, max_age_min=36 * 60.0)
    today = [r for r in runs if _started(r).date() == now.date()]
    if not today:
        due = pacific(dt.datetime.combine(now.date(), SILVER_STARTS_UTC, dt.UTC))
        return Finding(name, False, f"no run has started yet today; it is due at {due}")
    return job_finding(name, today, now, max_age_min=24 * 60.0)


def summary_markdown(findings: Sequence[Finding], now: dt.datetime) -> str:
    lines = [
        f"## Watchdog {now.astimezone(PACIFIC).strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "| | check | detail |",
        "|---|---|---|",
    ]
    lines += [f"| {'ok' if f.ok else '**FAIL**'} | {f.name} | {f.detail} |" for f in findings]
    return "\n".join(lines)


def status_payload(findings: Sequence[Finding], now: dt.datetime) -> dict[str, Any]:
    """The small file the website reads every 15 minutes (`status.json`).

    It says when the probes ran and what each found, so a visitor can tell "the pipeline has a
    problem" (a finding fails) from "publishing stopped" (checked_at is old).
    """

    return {
        "checked_at": now.isoformat(),
        "findings": [{"name": f.name, "ok": f.ok, "detail": f.detail} for f in findings],
    }
