"""Summarize landed GTFS-Realtime snapshots: lag, change between pulls, and gaps.

Usage: uv run python scripts/landing_report.py <dir with <source>/<epoch_ms>.pb files>
"""

import datetime as dt
import sys
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as pb

GAP_LIMIT_SECONDS = 300


def _clock(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.UTC).strftime("%H:%M:%S")


def _report(source_dir: Path, source: str) -> None:
    print(f"== {source}")
    previous: dict[str, int] = {}
    last_landed: int | None = None
    for path in sorted(source_dir.glob("*.pb")):
        if not path.stem.isdigit():
            continue  # manual smoke-test files are not collector output
        landed = int(path.stem) // 1000
        feed = pb.FeedMessage()
        feed.ParseFromString(path.read_bytes())
        header = int(feed.header.timestamp)
        current: dict[str, int] = {}
        for entity in feed.entity:
            if source == "vehicle_positions":
                current[entity.vehicle.vehicle.id] = int(entity.vehicle.timestamp)
            elif source == "trip_updates":
                current[entity.id] = int(entity.trip_update.timestamp)
            else:
                current[entity.id] = 0
        changed = sum(1 for key, value in current.items() if previous.get(key) != value)
        gap = "" if last_landed is None else f" gap={landed - last_landed}s"
        flag = " <-- GAP" if last_landed and landed - last_landed > GAP_LIMIT_SECONDS else ""
        print(
            f"landed {_clock(landed)} header {_clock(header)} lag={landed - header}s "
            f"entities={len(feed.entity)} keys={len(current)} new_or_changed={changed}{gap}{flag}"
        )
        previous, last_landed = current, landed


def main() -> None:
    base = Path(sys.argv[1])
    for source in ("vehicle_positions", "trip_updates", "alerts"):
        if (base / source).is_dir():
            _report(base / source, source)


if __name__ == "__main__":
    main()
