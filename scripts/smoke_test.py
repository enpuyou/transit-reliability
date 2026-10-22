# ruff: noqa: E501  (one long report line per check)
"""Smoke test after a deploy: is what runs in production what the repository says, and does it work?

    uv run python scripts/smoke_test.py --profile enpu   # by hand, after `databricks bundle deploy -t prod`
    python3 scripts/smoke_test.py                         # in CI (DATABRICKS_HOST/TOKEN from the environment)

Read-only, takes about a minute, spends no Databricks compute:
1. every prod job exists and has exactly the steps (task keys) listed in resources/*.yml;
2. the newest run of each scheduled job did not fail;
3. the live map's relay answers (bus file, trip memory, traffic), the same checks as the watchdog;
4. the public site serves its pages and data files.
Exit code 1 if anything is wrong, with one line per problem. Stdlib only.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_reliability.monitoring.watchdog import (  # noqa: E402
    RELAY_URL,
    relay_memory_finding,
    relay_traffic_finding,
    relay_vehicles_finding,
)

SITE = "https://enpuyou.github.io/transit-reliability/"
SITE_FILES = (
    "index.html",
    "map.html",
    "map.js",
    "map/routes.json",
    "map/shapes.json",
    "data/site.json",
)
JOB_FILES = {"ingest": "ingest_job", "monitor": "monitor_job", "silver": "silver_job"}
UA = {"User-Agent": "transit-reliability smoke test"}


def cli(args: list[str], profile: str | None) -> object:
    cmd = ["databricks", *args, "-o", "json"] + (["--profile", profile] if profile else [])
    done = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def expected_tasks(resource: str) -> set[str]:
    """Task keys listed in resources/<resource>.yml (a plain scan: every 'task_key:' that starts a task)."""

    text = Path(f"resources/{resource}.yml").read_text()
    return set(re.findall(r"^\s*- task_key:\s*(\S+)", text, flags=re.M))


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", help="CLI profile (omit in CI)")
    args = parser.parse_args()
    problems: list[str] = []
    lines: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        lines.append(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            problems.append(f"{name}: {detail}")

    jobs = {j["settings"]["name"]: j for j in cli(["jobs", "list"], args.profile)}  # type: ignore[union-attr]
    for short, resource in JOB_FILES.items():
        name = f"transit-reliability-prod-{short}"
        job = jobs.get(name)
        if not job:
            check(name, False, "not found in the workspace")
            continue
        full = cli(["jobs", "get", str(job["job_id"])], args.profile)
        deployed = {t["task_key"] for t in full["settings"]["tasks"]}  # type: ignore[index]
        want = expected_tasks(resource)
        missing, extra = want - deployed, deployed - want
        check(
            f"{name} steps",
            not missing and not extra,
            f"{len(deployed)} steps"
            + (f"; missing {sorted(missing)}" if missing else "")
            + (f"; not in the repository {sorted(extra)}" if extra else ""),
        )
        runs = cli(
            ["jobs", "list-runs", "--job-id", str(job["job_id"]), "--limit", "5"], args.profile
        )
        finished = [r for r in runs if (r.get("state") or {}).get("result_state")]  # type: ignore[union-attr]
        if finished:
            r = finished[0]
            when = dt.datetime.fromtimestamp(r["start_time"] / 1000, dt.UTC).strftime(
                "%m-%d %H:%M UTC"
            )
            check(
                f"{name} newest finished run",
                r["state"]["result_state"] == "SUCCESS",
                f"{when}: {r['state']['result_state']}",
            )
        else:
            check(f"{name} newest finished run", False, "no finished run")

    now = dt.datetime.now(dt.UTC)
    status, body = get(RELAY_URL + "/vehicles")
    f = relay_vehicles_finding(status, len(body))
    check(f.name, f.ok, f.detail)
    status, body = get(RELAY_URL + "/memory/status")
    f = relay_memory_finding(json.loads(body) if status == 200 else None, now)
    check(f.name, f.ok, f.detail)
    status, body = get(RELAY_URL + "/traffic/segment?lat=47.6205&lon=-122.3212")
    f = relay_traffic_finding(status, json.loads(body) if status == 200 else None)
    check(f.name, f.ok, f.detail)

    for path in SITE_FILES:
        status, body = get(SITE + path)
        check(
            f"site {path}", status == 200 and len(body) > 0, f"HTTP {status}, {len(body):,} bytes"
        )

    print("\n".join(lines))
    if problems:
        print(f"\n{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
