# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Health checks run at the end of the daily job; the task fails when any check fails.

Checks (thresholds are deliberately loose, to catch breakage and not normal variation):
- silver is not far behind bronze (a stale silver was the failure on 2026-10-16 to 18);
- for each complete service date, arrival coverage is not collapsed (healthy days were 85 to 87
  percent; a day with a feed outage still reaches about 60 percent);
- every scheduled trip has a trip_performance row;
- the stop-visit invariants hold (estimate inside its interval, departure not before arrival, stop
  order).
"""

import datetime as dt
from typing import Any

from transit_reliability.reliability.health import Check, failures

MAX_SILVER_LAG_HOURS = 3.0
MIN_ARRIVAL_COVERAGE = 0.5
COMPLETE_AFTER_DAYS = 2  # a service date is complete this many days later (after-midnight trips)
LOOKBACK_DAYS = 4


def run_health_checks(spark: Any, prefix: str) -> None:
    silver = f"workspace.{prefix}_silver"
    gold = f"workspace.{prefix}_gold"
    checks: list[Check] = []

    lag = spark.sql(
        f"""SELECT (unix_timestamp((SELECT max(ingested_at) FROM workspace.{prefix}_bronze.vehicle_positions))
                    - unix_timestamp(greatest((SELECT max(bronze_ingested_at) FROM {silver}.vehicle_positions),
                                              (SELECT max(bronze_ingested_at) FROM {silver}.quarantine_vehicle_positions)))) / 3600.0 AS h"""
    ).collect()[0]["h"]
    checks.append(
        Check("silver_lag_hours", None if lag is None else float(lag), maximum=MAX_SILVER_LAG_HOURS)
    )

    today = dt.datetime.now(dt.UTC).date()
    last_complete = today - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    first = today - dt.timedelta(days=LOOKBACK_DAYS)
    coverage = spark.sql(
        f"""SELECT service_date, avg(CASE WHEN arr_est IS NOT NULL THEN 1.0 ELSE 0.0 END) AS c
            FROM {silver}.stop_visits WHERE service_date BETWEEN DATE'{first}' AND DATE'{last_complete}' GROUP BY 1"""
    ).collect()
    if not coverage:
        checks.append(Check("arrival_coverage", None, minimum=MIN_ARRIVAL_COVERAGE))
    for r in coverage:
        checks.append(
            Check(
                f"arrival_coverage {r['service_date']}", float(r["c"]), minimum=MIN_ARRIVAL_COVERAGE
            )
        )

    gap = spark.sql(
        f"""SELECT (SELECT count(*) FROM {silver}.scheduled_trips WHERE service_date BETWEEN DATE'{first}' AND DATE'{last_complete}')
                 - (SELECT count(*) FROM {gold}.trip_performance WHERE service_date BETWEEN DATE'{first}' AND DATE'{last_complete}') AS g"""
    ).collect()[0]["g"]
    checks.append(
        Check(
            "scheduled_trips_without_performance_row",
            None if gap is None else float(gap),
            maximum=0.0,
        )
    )

    bad = spark.sql(
        f"""WITH v AS (SELECT *, lag(arr_est) OVER (PARTITION BY service_date, trip_id ORDER BY stop_sequence) AS prev_arr
                       FROM {silver}.stop_visits WHERE service_date >= DATE'{first}')
            SELECT sum(CASE WHEN arr_est < arr_lo OR arr_est > arr_hi OR dep_est < arr_est OR arr_est < prev_arr THEN 1 ELSE 0 END) AS n FROM v"""
    ).collect()[0]["n"]
    checks.append(
        Check("stop_visit_invariant_violations", None if bad is None else float(bad), maximum=0.0)
    )

    for c in checks:
        print(f"{'ok  ' if not failures([c]) else 'FAIL'} {c.name} = {c.value}")
    problems = failures(checks)
    if problems:
        raise RuntimeError("health checks failed: " + "; ".join(problems))
