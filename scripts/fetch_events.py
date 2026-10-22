"""Fetch upcoming Seattle-area events (games, concerts, shows) from Ticketmaster into JSON files.

    TICKETMASTER_KEY=... python3 scripts/fetch_events.py --days 21 --out events_out

One file per result page, named `<epoch_ms>-p<page>.json`, holding Ticketmaster's answer as
received, except that the API key Ticketmaster copies into its own page links is removed (a key
must never land in our storage). The GitHub workflow `events.yml` runs this once a day and uploads
the files to the landing volume, because Databricks Free Edition blocks most outbound internet.
Stdlib only.

Ticketmaster Discovery API, free key: 5,000 calls a day, 5 a second; deep paging stops at about
1,000 results, so the search is limited to 30 miles around downtown and the next DAYS days (a few
hundred events).
"""

import argparse
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://app.ticketmaster.com/discovery/v2/events.json"
CENTRE = "47.6062,-122.3321"  # downtown Seattle
RADIUS_MILES = 30
PAGE_SIZE = 200
MAX_PAGES = 5  # Ticketmaster stops at about 1,000 results
USER_AGENT = "transit-reliability (https://github.com/enpuyou/transit-reliability)"
KEY_IN_TEXT = re.compile(r"apikey=[^&\"'\s]+")


def scrub(text: str) -> str:
    """Remove the API key wherever Ticketmaster echoed it (its page links include it)."""

    return KEY_IN_TEXT.sub("apikey=REMOVED", text)


def fetch_page(key: str, start: dt.datetime, end: dt.datetime, page: int) -> str:
    query = urllib.parse.urlencode(
        {
            "apikey": key,
            "latlong": CENTRE,
            "radius": RADIUS_MILES,
            "unit": "miles",
            "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": PAGE_SIZE,
            "page": page,
            "sort": "date,asc",
        }
    )
    request = urllib.request.Request(f"{BASE}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except Exception as error:
            if attempt == 3:
                # never let the key reach a log through the error message
                raise RuntimeError(scrub(str(error))) from None
            time.sleep(3 * attempt)
    raise RuntimeError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    key = os.environ.get("TICKETMASTER_KEY", "")
    if not key:
        raise SystemExit("TICKETMASTER_KEY is not set")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = dt.datetime.now(dt.UTC).replace(microsecond=0)
    end = start + dt.timedelta(days=args.days)
    stamp = int(time.time() * 1000)
    total, pages = 0, 0
    for page in range(MAX_PAGES):
        text = scrub(fetch_page(key, start, end, page))
        if key in text:
            raise SystemExit("the API key is still in the answer; refusing to save it")
        data = json.loads(text)
        events = data.get("_embedded", {}).get("events", [])
        (out / f"{stamp}-p{page}.json").write_text(text)
        total += len(events)
        pages += 1
        if page + 1 >= data.get("page", {}).get("totalPages", 0):
            break
        time.sleep(0.5)  # well under 5 requests a second
    print(f"saved {pages} page(s), {total} events, {start:%Y-%m-%d} to {end:%Y-%m-%d}, in {out}")


if __name__ == "__main__":
    main()
