"""Read-only report: which raw landed files could be deleted, and what blocks the rest.

This script NEVER deletes anything. It lists old raw files, checks each one against the
`source_file` column of the bronze table, and groups them (see
`transit_reliability.retention`). Deleting is a separate, manual step; see decision 0005.

Usage:
  uv run python scripts/raw_retention_report.py [--source trip_updates] [--keep-days 30]
      [--prefix transit] [--silver-verified-through YYYY-MM-DD]

Without --silver-verified-through nothing is ever reported as deletable.
Needs the Databricks CLI profile `enpu`.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from transit_reliability.retention import RawFile, plan_retention  # noqa: E402

ROOT = "dbfs:/Volumes/workspace/transit_landing/raw"
PROFILE = "enpu"


def _cli(*args: str) -> str:
    result = subprocess.run(
        ["databricks", *args, "--profile", PROFILE], capture_output=True, text=True, check=False
    )
    if result.returncode:
        sys.exit(f"databricks {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def _old_folders(source: str, cutoff: dt.date) -> list[str]:
    names = _cli("fs", "ls", f"{ROOT}/{source}").split()
    return sorted(n for n in names if n.startswith("dt=") and n[3:] < cutoff.isoformat())


def _landed(source: str, folders: list[str]) -> list[RawFile]:
    files = []
    for folder in folders:
        listing = json.loads(_cli("fs", "ls", "-o", "json", f"{ROOT}/{source}/{folder}"))
        files += [RawFile(f"{ROOT}/{source}/{folder}/{e['name']}", e["size"]) for e in listing]
    return files


def _ingested(source: str, prefix: str, cutoff: dt.date) -> set[str]:
    statement = (
        f"SELECT DISTINCT source_file FROM workspace.{prefix}_bronze.{source} "
        f"WHERE regexp_extract(source_file, 'dt=([0-9-]+)', 1) < '{cutoff.isoformat()}'"
    )
    warehouses = json.loads(_cli("warehouses", "list", "-o", "json"))
    body = {"warehouse_id": warehouses[0]["id"], "wait_timeout": "50s", "statement": statement}
    reply = json.loads(_cli("api", "post", "/api/2.0/sql/statements", "--json", json.dumps(body)))
    if reply["status"]["state"] != "SUCCEEDED":
        sys.exit(f"SQL failed: {reply['status']}")
    return {row[0] for row in reply.get("result", {}).get("data_array", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", default="trip_updates")
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--prefix", default="transit", help="schema prefix: transit or dev_transit")
    parser.add_argument("--silver-verified-through", type=dt.date.fromisoformat)
    args = parser.parse_args()

    today = dt.datetime.now(dt.UTC).date()
    cutoff = today - dt.timedelta(days=args.keep_days)
    folders = _old_folders(args.source, cutoff)
    landed = _landed(args.source, folders)
    ingested = _ingested(args.source, args.prefix, cutoff) if landed else set()
    plan = plan_retention(landed, ingested, today, args.keep_days, args.silver_verified_through)

    print(f"{args.source}: files older than {cutoff} ({args.keep_days} days kept): {len(landed)}")
    for label, files in (
        ("blocked, NOT in bronze (never delete)", plan.blocked_not_in_bronze),
        ("in bronze, waiting for silver verification", plan.waiting_for_silver),
        ("deletable (old, in bronze, silver verified)", plan.deletable),
        ("ignored (smoke or non-collector)", plan.ignored),
    ):
        megabytes = sum(f.size for f in files) / 1e6
        print(f"  {label}: {len(files)} files, {megabytes:,.1f} MB")
    print("Nothing was deleted. This report is read-only.")


if __name__ == "__main__":
    main()
