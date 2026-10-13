"""Measure how often KCM publishes new feed versions and how often buses report.

Polls the vehicle-positions feed every few seconds and prints each new version, then a
summary of per-vehicle gaps between new GPS fixes.

Usage: uv run python scripts/feed_cadence.py [seconds_to_run]   (default 130)
"""

import collections
import datetime as dt
import sys
import time
import urllib.request

from google.transit import gtfs_realtime_pb2 as pb

URL = "https://s3.amazonaws.com/kcm-alerts-realtime-prod/vehiclepositions.pb"
POLL_SECONDS = 4


def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 130
    fixes: dict[str, list[int]] = collections.defaultdict(list)
    headers: list[int] = []
    started = time.time()
    while time.time() - started < duration:
        feed = pb.FeedMessage()
        feed.ParseFromString(urllib.request.urlopen(URL).read())
        header = int(feed.header.timestamp)
        if not headers or header != headers[-1]:
            headers.append(header)
            when = dt.datetime.fromtimestamp(header, dt.UTC).strftime("%H:%M:%S")
            print(f"new feed version, header time {when}Z")
            for entity in feed.entity:
                stamp = int(entity.vehicle.timestamp)
                vehicle = entity.vehicle.vehicle.id
                if stamp and (not fixes[vehicle] or fixes[vehicle][-1] != stamp):
                    fixes[vehicle].append(stamp)
        time.sleep(POLL_SECONDS)
    spacing = sorted(b - a for a, b in zip(headers, headers[1:], strict=False))
    gaps = sorted(b - a for seq in fixes.values() for a, b in zip(seq, seq[1:], strict=False))
    if spacing:
        print(f"feed versions: {len(headers)}, median spacing {spacing[len(spacing) // 2]}s")
    if gaps:
        n = len(gaps)
        print(f"per-bus gap between fixes: median {gaps[n // 2]}s, p90 {gaps[int(n * 0.9)]}s")


if __name__ == "__main__":
    main()
