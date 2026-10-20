"""Pure helpers for the proof report: time zones, distances, which stops to show, tables."""

import datetime as dt
import math
from collections.abc import Sequence
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
FEET_PER_METER = 3.28084
EARTH_RADIUS_M = 6_371_000.0


def pacific(value: str | None) -> str:
    """'2026-10-16T20:09:59.000Z' -> '13:09:59' (the wall clock a rider would see)."""

    if not value:
        return "-"
    moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.astimezone(PACIFIC).strftime("%H:%M:%S")


def feet_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in feet (haversine)."""

    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)) * FEET_PER_METER


def evenly_spaced(items: Sequence[int], count: int) -> list[int]:
    """Up to `count` items spread from first to last (always including both ends)."""

    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join("" if c is None else str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)
