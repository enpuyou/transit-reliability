"""Run a suite of checks, keep every result in `<prefix>_ops.check_results`, fail on real failures.

The results are written BEFORE the task fails, so a failing run leaves its evidence behind.
"""

import datetime as dt
from typing import Any

from transit_reliability.audit.core import CheckResult, CheckSpec, render, run_checks
from transit_reliability.audit.runners import spark_runner
from transit_reliability.monitoring.daily import complete_day, daily_checks
from transit_reliability.monitoring.hourly import hourly_checks

TABLE = "check_results"
DDL = (
    "run_ts timestamp, suite string, check_name string, status string, value double, rule string, "
    "severity string, error string, explanation string, sql string, service_date date"
)


def result_rows(
    results: list[CheckResult], suite: str, run_ts: dt.datetime, day: dt.date | None
) -> list[tuple[Any, ...]]:
    return [
        (
            run_ts,
            suite,
            r.name,
            r.status,
            r.value,
            r.rule,
            r.severity,
            r.error,
            r.explanation,
            r.sql,
            day,
        )
        for r in results
    ]


def blocking_failures(results: list[CheckResult]) -> list[str]:
    out = []
    for r in results:
        if r.status == "fail":
            shown = r.error if r.value is None and r.error else r.value
            out.append(f"{r.name} = {shown} (rule: {r.rule})")
    return out


def specs_for(suite: str, prefix: str, today: dt.date) -> tuple[list[CheckSpec], dt.date | None]:
    if suite == "hourly":
        return hourly_checks(prefix), None
    if suite == "daily":
        day = complete_day(today)
        return daily_checks(prefix, day), day
    raise ValueError(f"unknown suite {suite!r}")


def run_monitor(spark: Any, prefix: str, suite: str, day: dt.date | None = None) -> None:
    now = dt.datetime.now(dt.UTC)
    specs, default_day = specs_for(suite, prefix, now.date())
    if suite == "daily" and day is not None:
        specs, default_day = daily_checks(prefix, day), day
    results = run_checks(specs, spark_runner(spark))
    print(render(results))

    table = f"workspace.{prefix}_ops.{TABLE}"
    rows = result_rows(results, suite, now.replace(tzinfo=None), default_day)
    spark.createDataFrame(rows, DDL).write.format("delta").mode("append").saveAsTable(table)
    print(f"wrote {len(rows)} results to {table}")

    problems = blocking_failures(results)
    if problems:
        raise RuntimeError(f"{len(problems)} {suite} check(s) failed: " + "; ".join(problems))
