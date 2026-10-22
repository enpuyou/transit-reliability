# ruff: noqa: E501  (SQL is kept on readable lines)
"""Typical lateness at each timetable stop, exported for the live map's popup (decision 0025).

Key "route|direction|stop" -> "daytype.band" -> [median seconds late, 85th percentile seconds late,
number of trips]. Negative means early. Only timepoints (stops with a published time), only arrival
estimates whose interval is at most 120 s wide, only groups with at least 5 trips, and only complete
past days: the map shows "what usually happens", never today. The time bands match gold segments.
"""

import datetime as dt
import json
import os
from typing import Any

from transit_reliability.bronze.schema import volume_path

WINDOW_DAYS = 28
COMPLETE_AFTER_DAYS = 2
MIN_TRIPS = 5
MAX_WIDTH_S = 120
HISTORY_FILE = ("public", "stop_history.json")
TRIP_HISTORY_DIR = ("public", "trip_history")
EVENTS_FILE = ("public", "events.json")
MIN_DAYS_PER_TRIP = 3

_PT = "from_utc_timestamp(sched_arr, 'America/Los_Angeles')"
_BAND = (
    f"CASE WHEN hour({_PT}) BETWEEN 6 AND 8 THEN 'am' WHEN hour({_PT}) BETWEEN 9 AND 14 THEN 'mid' "
    f"WHEN hour({_PT}) BETWEEN 15 AND 18 THEN 'pm' WHEN hour({_PT}) BETWEEN 19 AND 23 THEN 'eve' ELSE 'night' END"
)
_DAY = "CASE dayofweek(service_date) WHEN 1 THEN 'sun' WHEN 7 THEN 'sat' ELSE 'wk' END"


def history_sql(prefix: str, first_day: dt.date, last_day: dt.date) -> str:
    return f"""SELECT route_id, direction_id, stop_id, {_DAY} AS d, {_BAND} AS b,
        percentile(arr_delay_s, 0.5) AS med, percentile(arr_delay_s, 0.85) AS p85, count(*) AS n
        FROM workspace.{prefix}_silver.stop_visits
        WHERE timepoint = 1 AND arr_est IS NOT NULL AND arr_delay_s IS NOT NULL
          AND unix_timestamp(arr_hi) - unix_timestamp(arr_lo) <= {MAX_WIDTH_S}
          AND service_date BETWEEN DATE'{first_day.isoformat()}' AND DATE'{last_day.isoformat()}'
        GROUP BY 1, 2, 3, 4, 5 HAVING count(*) >= {MIN_TRIPS}"""


def history_payload(
    rows: list[tuple[Any, ...]], first_day: dt.date, last_day: dt.date
) -> dict[str, Any]:
    hist: dict[str, dict[str, list[int]]] = {}
    for route, direction, stop, day, band, med, p85, n in rows:
        hist.setdefault(f"{route}|{direction}|{stop}", {})[f"{day}.{band}"] = [
            round(float(med)),
            round(float(p85)),
            int(n),
        ]
    return {"first_day": first_day.isoformat(), "last_day": last_day.isoformat(), "h": hist}


def export_map_history(spark: Any, prefix: str, state_volume: str) -> None:
    silver = f"workspace.{prefix}_silver.stop_visits"
    oldest, newest = spark.sql(
        f"SELECT min(service_date), max(service_date) FROM {silver} WHERE arr_est IS NOT NULL"
    ).first()
    last_day = newest - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    first_day = max(last_day - dt.timedelta(days=WINDOW_DAYS - 1), oldest)
    rows = [tuple(r) for r in spark.sql(history_sql(prefix, first_day, last_day)).collect()]
    payload = history_payload(rows, first_day, last_day)
    if not payload["h"]:
        raise RuntimeError("stop history is empty; refusing to publish an empty file")
    path = volume_path(state_volume, *HISTORY_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(
        f"wrote {path}: {len(text):,} bytes, {len(payload['h'])} stops, {first_day} to {last_day}"
    )


def trip_history_sql(prefix: str, first_day: dt.date, last_day: dt.date) -> str:
    """For each trip and stop: how late this exact trip usually reached the stop (median, 85th percentile, days)."""

    return f"""SELECT route_id, trip_id, stop_id, percentile(arr_delay_s, 0.5) AS med, percentile(arr_delay_s, 0.85) AS p85,
        count(*) AS n
        FROM workspace.{prefix}_silver.stop_visits
        WHERE arr_est IS NOT NULL AND arr_delay_s IS NOT NULL
          AND unix_timestamp(arr_hi) - unix_timestamp(arr_lo) <= {MAX_WIDTH_S}
          AND service_date BETWEEN DATE'{first_day.isoformat()}' AND DATE'{last_day.isoformat()}'
        GROUP BY route_id, trip_id, stop_id HAVING count(*) >= {MIN_DAYS_PER_TRIP}"""


def trip_history_payloads(
    rows: list[tuple[Any, ...]], first_day: dt.date, last_day: dt.date
) -> dict[str, dict[str, Any]]:
    """One payload per route: {"first_day", "last_day", "t": {trip: {stop: [median s late, 85th pct, days]}}}."""

    out: dict[str, dict[str, Any]] = {}
    for route, trip, stop, med, p85, n in rows:
        r = out.setdefault(
            str(route),
            {"first_day": first_day.isoformat(), "last_day": last_day.isoformat(), "t": {}},
        )
        r["t"].setdefault(str(trip), {})[str(stop)] = [round(float(med)), round(float(p85)), int(n)]
    return out


def export_trip_history(spark: Any, prefix: str, state_volume: str) -> None:
    silver = f"workspace.{prefix}_silver.stop_visits"
    oldest, newest = spark.sql(
        f"SELECT min(service_date), max(service_date) FROM {silver} WHERE arr_est IS NOT NULL"
    ).first()
    last_day = newest - dt.timedelta(days=COMPLETE_AFTER_DAYS)
    first_day = max(last_day - dt.timedelta(days=WINDOW_DAYS - 1), oldest)
    rows = [tuple(r) for r in spark.sql(trip_history_sql(prefix, first_day, last_day)).collect()]
    payloads = trip_history_payloads(rows, first_day, last_day)
    if not payloads:
        raise RuntimeError("trip history is empty; refusing to publish empty files")
    folder = volume_path(state_volume, *TRIP_HISTORY_DIR)
    os.makedirs(folder, exist_ok=True)
    total = 0
    for route, payload in payloads.items():
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with open(os.path.join(folder, f"{route}.json"), "w", encoding="utf-8") as handle:
            handle.write(text)
        total += len(text)
    print(
        f"wrote {len(payloads)} route files to {folder}: {total:,} bytes, {len(rows)} trip-stop rows"
    )


def events_payload(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Group event_routes rows (event, name, segment, start, status, venue, lat, lon, large, seats,
    route, nearest stop m) into one entry per event with the routes that pass near it."""

    by: dict[str, dict[str, Any]] = {}
    for eid, name, segment, start, status, venue, lat, lon, large, seats, route, near in rows:
        e = by.setdefault(
            str(eid),
            {
                "id": str(eid),
                "name": name,
                "kind": segment,
                "start": int(start.timestamp()) if hasattr(start, "timestamp") else start,
                "status": status,
                "venue": venue,
                "at": [round(float(lon), 5), round(float(lat), 5)],
                "large": bool(large),
                "seats": seats,
                "routes": {},
            },
        )
        e["routes"][str(route)] = int(near)
    return sorted(by.values(), key=lambda e: (e["start"] or 0, e["name"] or ""))


def export_events(spark: Any, prefix: str, state_volume: str) -> None:
    """Events from 6 hours ago to 3 days ahead with the routes near them, for the live map."""

    table = f"workspace.{prefix}_gold.event_routes"
    if not spark.catalog.tableExists(table):
        print("no event_routes table yet; no events file")
        return
    rows = [
        tuple(r)
        for r in spark.sql(
            f"SELECT event_id, name, segment, start_ts, status, venue_name, latitude, longitude, large_venue, "
            f"venue_seats, route_id, nearest_stop_m FROM {table} "
            f"WHERE start_ts BETWEEN current_timestamp() - INTERVAL 6 HOURS AND current_timestamp() + INTERVAL 3 DAYS"
        ).collect()
    ]
    path = volume_path(state_volume, *EVENTS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = events_payload(rows)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {path}: {len(payload)} events")
