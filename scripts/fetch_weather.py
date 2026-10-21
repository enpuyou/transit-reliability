"""Fetch recent weather observations from the National Weather Service into local JSON files.

    python3 scripts/fetch_weather.py --hours 6 --out weather_out

One file per station, named `<epoch_ms>-<station>.json`, holding the observations exactly as
received. The GitHub workflow `weather.yml` runs this and uploads the files to the landing volume,
because Databricks Free Edition blocks most outbound internet. The service keeps about the last
7 days, so a missed run is recovered by the next one. Stdlib only. Public US government data, no
key; the service asks callers to identify themselves with a User-Agent.
"""

import argparse
import datetime as dt
import json
import time
import urllib.request
from pathlib import Path

STATIONS = ("KSEA", "KBFI", "KRNT")  # Sea-Tac, Boeing Field, Renton
BASE = "https://api.weather.gov"
USER_AGENT = "transit-reliability (https://github.com/enpuyou/transit-reliability)"
MAX_PAGES = 20


def get_json(url: str) -> dict:  # type: ignore[type-arg]
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    )
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)  # type: ignore[no-any-return]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * attempt)
    raise RuntimeError("unreachable")


def observed_at(feature: dict) -> dt.datetime:  # type: ignore[type-arg]
    return dt.datetime.fromisoformat(feature["properties"]["timestamp"].replace("Z", "+00:00"))


def fetch_station(  # type: ignore[type-arg]
    station: str, start: dt.datetime, end: dt.datetime
) -> list[dict]:
    """Observations of one station between start and end, newest first.

    The service's "next page" link drops the time window and keeps going back through its whole
    retention, so each page is filtered by timestamp and paging stops once a page passes start.
    """

    stamp = "%Y-%m-%dT%H:%M:%SZ"
    url: str | None = (
        f"{BASE}/stations/{station}/observations?start={start.strftime(stamp)}&end={end.strftime(stamp)}&limit=500"
    )
    features: list[dict] = []  # type: ignore[type-arg]
    for _ in range(MAX_PAGES):
        if not url:
            break
        data = get_json(url)
        page = data.get("features", [])
        features += [f for f in page if start <= observed_at(f) <= end]
        if not page or min(observed_at(f) for f in page) < start:
            break
        url = (data.get("pagination") or {}).get("next")
    return features


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=6.0, help="how far back to fetch")
    parser.add_argument("--out", default="weather_out")
    args = parser.parse_args()
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(hours=args.hours)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp_ms = int(end.timestamp() * 1000)
    failures = 0
    for station in STATIONS:
        try:
            features = fetch_station(station, start, end)
        except Exception as error:  # noqa: BLE001 - one failing station must not stop the others
            print(f"{station}: FAILED {error}")
            failures += 1
            continue
        (out / f"{stamp_ms}-{station}.json").write_text(
            json.dumps({"station": station, "fetched_at_ms": stamp_ms, "features": features})
        )
        print(f"{station}: {len(features)} observations")
    return 1 if failures == len(STATIONS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
