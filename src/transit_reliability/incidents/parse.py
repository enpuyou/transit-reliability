"""Read Seattle Fire 911 dispatch files (landed by scripts/fetch_incidents.py) into plain rows.

Pure functions, tested locally. Times in the source are Seattle local time with no zone; they are
turned into UTC here. Each dispatch gets a category, because only some can affect buses:
traffic (collisions), fire, medical, or other.
"""

import datetime as dt
import json
from typing import Any
from zoneinfo import ZoneInfo

SEATTLE = ZoneInfo("America/Los_Angeles")


def category(kind: str | None) -> str:
    """traffic, fire, medical or other, from the dispatch type text."""

    k = (kind or "").lower()
    if k.startswith("mvi") or "motor vehicle" in k or "collision" in k or "car fire" in k:
        return "traffic"
    if "fire" in k and "alarm" not in k:
        return "fire"
    if k.startswith(("aid", "medic", "low acuity", "trans to amr")):
        return "medical"
    return "other"


def local_to_utc(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    local = dt.datetime.fromisoformat(text.split(".")[0]).replace(tzinfo=SEATTLE)
    return local.astimezone(dt.UTC)


def parse_incidents(text: str, source_file: str, pulled_at: dt.datetime) -> list[dict[str, Any]]:
    rows = []
    for r in json.loads(text):
        rows.append(
            {
                "incident_number": r.get("incident_number"),
                "type": r.get("type"),
                "category": category(r.get("type")),
                "address": r.get("address"),
                "dispatched_ts": local_to_utc(r.get("datetime")),
                "latitude": float(r["latitude"]) if r.get("latitude") else None,
                "longitude": float(r["longitude"]) if r.get("longitude") else None,
                "source_file": source_file,
                "pulled_at": pulled_at,
            }
        )
    return rows
