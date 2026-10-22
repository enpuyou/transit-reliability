"""Read Ticketmaster event search answers (landed by scripts/fetch_events.py) into plain rows.

Pure functions, so they are tested locally. One row per event in a file: when it starts (UTC), the
venue and where it is, what kind of event, its status (onsale, cancelled, postponed, rescheduled),
and whether the venue is a large one (big crowds are what can slow buses).
"""

import datetime as dt
import json
from typing import Any

# Large venues by name (Ticketmaster gives no capacity). Approximate public seat counts, to sort out
# which events can move crowds big enough to matter; unverified: adjust when the data disagrees.
LARGE_VENUES = {
    "lumen field": 68000,
    "husky stadium": 70000,
    "t-mobile park": 47000,
    "climate pledge arena": 18000,
    "alaska airlines arena at hec edmundson pavilion": 10000,
    "accesso showare center": 7000,
    "wamu theater": 7000,
    "mccaw hall": 2900,
    "paramount theatre": 2800,
    "benaroya hall - s. mark taper foundation auditorium": 2500,
    "benaroya hall": 2500,
    "5th avenue theatre": 2100,
    "moore theatre": 1800,
}
LARGE_FROM = 1500  # seats: a venue this big or bigger counts as large


def venue_seats(name: str | None) -> int | None:
    """Approximate seats of a known large venue (matched ignoring case and a leading 'The')."""

    key = (name or "").strip().lower()
    key = key[4:] if key.startswith("the ") else key
    return LARGE_VENUES.get(key)


def _first(items: Any) -> dict[str, Any]:
    return items[0] if isinstance(items, list) and items else {}


def _start_utc(dates: dict[str, Any]) -> dt.datetime | None:
    stamp = (dates.get("start") or {}).get("dateTime")
    if not stamp:
        return None  # a date with no time ("TBA"): cannot be placed in an hour
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(dt.UTC)


def parse_events(text: str, source_file: str, pulled_at: dt.datetime) -> list[dict[str, Any]]:
    """Every event in one saved answer, as rows."""

    data = json.loads(text)
    rows = []
    for e in (data.get("_embedded") or {}).get("events", []):
        venue = _first((e.get("_embedded") or {}).get("venues"))
        location = venue.get("location") or {}
        kind = _first(e.get("classifications"))
        dates = e.get("dates") or {}
        name = venue.get("name")
        seats = venue_seats(name)
        rows.append(
            {
                "event_id": e.get("id"),
                "name": e.get("name"),
                "segment": (kind.get("segment") or {}).get("name"),
                "genre": (kind.get("genre") or {}).get("name"),
                "start_ts": _start_utc(dates),
                "local_date": (dates.get("start") or {}).get("localDate"),
                "status": (dates.get("status") or {}).get("code"),
                "venue_id": venue.get("id"),
                "venue_name": name,
                "latitude": float(location["latitude"]) if location.get("latitude") else None,
                "longitude": float(location["longitude"]) if location.get("longitude") else None,
                "venue_seats": seats,
                "large_venue": bool(seats and seats >= LARGE_FROM),
                "source_file": source_file,
                "pulled_at": pulled_at,
            }
        )
    return rows


def pulled_at_from_name(name: str) -> dt.datetime:
    """Files are named `<epoch_ms>-p<page>.json` by the fetcher."""

    return dt.datetime.fromtimestamp(int(name.split("-")[0]) / 1000, dt.UTC)
