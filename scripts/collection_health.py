"""Check collector health from the files that actually landed (ground truth).

Every cancelled or failed Worker run (for example one that exceeded the 10 ms CPU limit)
shows up as a hole in the landed files. This script finds those holes without needing
Cloudflare analytics:

  trip_updates       one file per run (every 60 s)  -> a gap over 90 s means missed runs
  vehicle_positions  three files per run (every ~20 s) -> a gap over 50 s means missed fetches
                     (a deliberately skipped duplicate version leaves about 40 s)

A gap in trip updates with vehicle positions still arriving means the run partly worked
(for example the trip fetch failed); a gap in both means the whole run was lost.

Usage: uv run python scripts/collection_health.py [YYYY-MM-DD ...]   (default: today, UTC)
Needs the Databricks CLI profile `enpu`.
"""

import datetime as dt
import subprocess
import sys

ROOT = "dbfs:/Volumes/workspace/transit_landing/raw"
PROFILE = "enpu"
# Before this, the Worker fetched vehicle positions once per run instead of three times.
THREE_FETCH_START = dt.datetime(2026, 10, 13, 23, 26, tzinfo=dt.UTC)
TRIP_GAP_SECONDS = 90
VEHICLE_GAP_SECONDS = 50


def _epochs(source: str, day: str) -> list[float]:
    out = subprocess.run(
        ["databricks", "fs", "ls", f"{ROOT}/{source}/dt={day}", "--profile", PROFILE],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return sorted(
        int(name[:-3]) / 1000 for name in out if name.endswith(".pb") and name[:-3].isdigit()
    )


def _clock(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.UTC).strftime("%H:%M:%S")


def _gaps(times: list[float], limit: int, since: float) -> list[tuple[float, float]]:
    return [(a, b) for a, b in zip(times, times[1:], strict=False) if a >= since and b - a > limit]


def report(day: str) -> None:
    trips = _epochs("trip_updates", day)
    vehicles = _epochs("vehicle_positions", day)
    since3 = THREE_FETCH_START.timestamp()
    trip_gaps = _gaps(trips, TRIP_GAP_SECONDS, 0)
    vehicle_gaps = _gaps(vehicles, VEHICLE_GAP_SECONDS, since3)
    print(f"== {day} UTC")
    print(f"trip_updates files: {len(trips)}, vehicle_positions files: {len(vehicles)}")
    missed_runs = sum(round((b - a) / 60) - 1 for a, b in trip_gaps)
    missed_fetches = sum(round((b - a) / 20) - 1 for a, b in vehicle_gaps)
    print(
        f"trip-update gaps over {TRIP_GAP_SECONDS}s: {len(trip_gaps)} "
        f"(about {missed_runs} runs lost)"
    )
    for a, b in trip_gaps:
        print(f"  {_clock(a)} -> {_clock(b)}  ({round(b - a)} s)")
    print(
        f"vehicle gaps over {VEHICLE_GAP_SECONDS}s: {len(vehicle_gaps)} "
        f"(about {missed_fetches} fetches lost)"
    )
    for a, b in vehicle_gaps:
        print(f"  {_clock(a)} -> {_clock(b)}  ({round(b - a)} s)")
    print("Note: a Worker deploy during a run's 40 s wait also cancels that run.")


def main() -> None:
    now = dt.datetime.now(dt.UTC)
    for day in sys.argv[1:] or [now.strftime("%Y-%m-%d")]:
        report(day)


if __name__ == "__main__":
    main()
