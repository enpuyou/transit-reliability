"""Fetch recent Seattle Fire Department 911 dispatches (City of Seattle open data) into a JSON file.

    python3 scripts/fetch_incidents.py --hours 30 --out incidents_out

One file per run, named `<epoch_ms>-incidents.json`, holding the dispatches exactly as received. The
GitHub workflow `incidents.yml` runs this every 2 hours and uploads the file to the landing volume
(Databricks blocks most outbound internet). Each run asks for the last 30 hours, so late or missed
runs lose nothing; silver keeps one row per incident. The dataset's times are Seattle local time
with no zone. Free, no key. Stdlib only.
"""

import argparse
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://data.seattle.gov/resource/kzjm-xkqj.json"
USER_AGENT = "transit-reliability (https://github.com/enpuyou/transit-reliability)"
SEATTLE = ZoneInfo("America/Los_Angeles")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=30)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    since = (dt.datetime.now(SEATTLE) - dt.timedelta(hours=args.hours)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    query = urllib.parse.urlencode(
        {"$where": f"datetime > '{since}'", "$order": "datetime DESC", "$limit": 5000}
    )
    request = urllib.request.Request(f"{URL}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode("utf-8")
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * attempt)
    rows = json.loads(text)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{int(time.time() * 1000)}-incidents.json").write_text(text)
    print(f"saved {len(rows)} dispatches since {since} (Seattle time) in {out}")


if __name__ == "__main__":
    main()
