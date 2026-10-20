"""External watchdog: ask Databricks what happened and judge it. Exit 1 when anything is wrong.

Runs hourly from GitHub Actions (.github/workflows/watchdog.yml) and by hand:

    uv run python scripts/watchdog.py --profile enpu

It never touches the SQL warehouse (which may be stopped); it uses only the jobs API and the landing
volume listing. Needs the Databricks CLI; in CI the host and token come from environment variables.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_reliability.monitoring.watchdog import (  # noqa: E402
    MAX_INGEST_RUN_AGE_MIN,
    MAX_LANDING_AGE_MIN,
    MAX_MONITOR_RUN_AGE_MIN,
    Finding,
    job_finding,
    landing_finding,
    silver_finding,
    status_payload,
    summary_markdown,
)

LANDING = "dbfs:/Volumes/workspace/transit_landing/raw"


def cli(args: list[str], profile: str | None) -> object:
    cmd = ["databricks", *args, "-o", "json"]
    if profile:
        cmd += ["--profile", profile]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(done.stderr.strip()[:300] or "databricks CLI failed")
    return json.loads(done.stdout)


def job_runs(profile: str | None, suffix: str) -> list[dict]:  # type: ignore[type-arg]
    jobs = cli(["jobs", "list"], profile)
    ids = [j["job_id"] for j in jobs if j["settings"]["name"].endswith(suffix)]  # type: ignore[attr-defined,union-attr]
    if len(ids) != 1:
        raise RuntimeError(f"expected one job named *{suffix}, found {len(ids)}")
    return cli(["jobs", "list-runs", "--job-id", str(ids[0]), "--limit", "30"], profile)  # type: ignore[return-value]


def landing_names(source: str, now: dt.datetime, profile: str | None) -> list[str]:
    names: list[str] = []
    for day in (now.date(), now.date() - dt.timedelta(days=1)):
        try:
            listing = cli(["fs", "ls", f"{LANDING}/{source}/dt={day}"], profile)
        except RuntimeError:
            continue  # the folder for a new UTC day does not exist until the first file lands
        names += [f["name"] for f in listing]  # type: ignore[union-attr]
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", help="CLI profile (omit in CI, where env vars are used)")
    parser.add_argument("--json-out", help="also write the findings as status.json for the website")
    args = parser.parse_args()
    now = dt.datetime.now(dt.UTC)
    findings: list[Finding] = []

    def guarded(name: str, make) -> None:  # type: ignore[no-untyped-def]
        try:
            findings.append(make())
        except Exception as error:  # noqa: BLE001 - an unanswerable question is a failed check
            findings.append(Finding(name, False, f"could not check: {error}"))

    for source in MAX_LANDING_AGE_MIN:
        guarded(
            f"landing_{source}_newest_file",
            lambda s=source: landing_finding(s, landing_names(s, now, args.profile), now),
        )
    guarded(
        "ingest_job",
        lambda: job_finding(
            "ingest_job", job_runs(args.profile, "-prod-ingest"), now, MAX_INGEST_RUN_AGE_MIN
        ),
    )
    guarded(
        "monitor_job",
        lambda: job_finding(
            "monitor_job", job_runs(args.profile, "-prod-monitor"), now, MAX_MONITOR_RUN_AGE_MIN
        ),
    )
    guarded(
        "silver_job_daily_run", lambda: silver_finding(job_runs(args.profile, "-prod-silver"), now)
    )

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(status_payload(findings, now)))
    report = summary_markdown(findings, now)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(report + "\n")
    bad = [f for f in findings if not f.ok]
    if bad:
        print(f"\n{len(bad)} problem(s): " + "; ".join(f"{f.name}: {f.detail}" for f in bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
