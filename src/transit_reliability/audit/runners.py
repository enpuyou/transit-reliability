"""Ways to run a check's SQL. Read-only: every check is a single SELECT.

From the shell (needs the Databricks CLI profile; `databricks api` ignores the bundle profile):

    uv run python -m transit_reliability.audit.runners --profile enpu --day 2026-10-16
"""

import argparse
import json
import subprocess
import time
from typing import Any

from transit_reliability.audit.catalog import all_checks
from transit_reliability.audit.core import Runner, render, run_checks


def _api(profile: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    cmd = ["databricks", "api", method, path, "--profile", profile]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(done.stderr[:300])
    return json.loads(done.stdout)


def first_value(response: dict[str, Any]) -> float | None:
    """The single number in a finished statement response, or None for no row or a NULL."""

    rows = response.get("result", {}).get("data_array") or []
    if not rows or not rows[0] or rows[0][0] is None:
        return None
    return float(rows[0][0])


def statement_runner(profile: str, warehouse_id: str) -> Runner:
    """A runner that sends each SQL to a SQL warehouse through the statement API."""

    def run(sql: str) -> float | None:
        body = {"warehouse_id": warehouse_id, "wait_timeout": "50s", "statement": sql}
        response = _api(profile, "post", "/api/2.0/sql/statements", body)
        while response["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(5)
            response = _api(profile, "get", f"/api/2.0/sql/statements/{response['statement_id']}")
        if response["status"]["state"] != "SUCCEEDED":
            raise RuntimeError(json.dumps(response["status"])[:300])
        return first_value(response)

    return run


def spark_runner(spark: Any) -> Runner:
    """A runner for a notebook or job: `spark.sql(sql)` and take the first cell."""

    def run(sql: str) -> float | None:
        value = spark.sql(sql).collect()[0][0]
        return None if value is None else float(value)

    return run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the independent data audit (read-only).")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--day", required=True, help="a complete service date, YYYY-MM-DD")
    parser.add_argument("--prefix", default="transit")
    args = parser.parse_args()
    listing = _api(args.profile, "get", "/api/2.0/sql/warehouses")
    warehouse_id = listing["warehouses"][0]["id"]
    results = run_checks(
        all_checks(args.prefix, args.day), statement_runner(args.profile, warehouse_id)
    )
    print(render(results))
    for r in results:
        if not r.passed:
            print(f"\n-- {r.name}: {r.explanation}\n{r.sql}")
            if r.error:
                print(f"-- error: {r.error}")


if __name__ == "__main__":
    main()
