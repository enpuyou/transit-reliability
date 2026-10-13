"""Parse GTFS-Realtime protobuf payloads into typed row dictionaries."""

from typing import Any, cast

from google.transit import gtfs_realtime_pb2


def _optional_value(message: Any, field: str) -> object | None:
    if message.HasField(field):
        return cast(object, getattr(message, field))
    return None


def _localized_text(message: Any) -> str | None:
    if not message.translation:
        return None
    return str(message.translation[0].text)


def parse_vehicle_positions(payload: bytes) -> list[dict[str, object]]:
    """Parse one vehicle-position feed into one row per vehicle entity."""

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    rows: list[dict[str, object]] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        trip = vehicle.trip
        position = vehicle.position
        rows.append(
            {
                "entity_id": str(entity.id),
                "feed_header_ts": int(feed.header.timestamp),
                "vehicle_id": str(vehicle.vehicle.id) if vehicle.vehicle.id else None,
                "vehicle_label": str(vehicle.vehicle.label) if vehicle.vehicle.label else None,
                "trip_id": str(trip.trip_id) if trip.trip_id else None,
                "route_id": str(trip.route_id) if trip.route_id else None,
                "direction_id": _optional_value(trip, "direction_id"),
                "start_date": str(trip.start_date) if trip.start_date else None,
                "start_time": str(trip.start_time) if trip.start_time else None,
                "latitude": float(position.latitude) if position.HasField("latitude") else None,
                "longitude": float(position.longitude) if position.HasField("longitude") else None,
                "bearing": _optional_value(position, "bearing"),
                "speed": _optional_value(position, "speed"),
                "current_stop_sequence": _optional_value(vehicle, "current_stop_sequence"),
                "stop_id": str(vehicle.stop_id) if vehicle.stop_id else None,
                "current_status": _optional_value(vehicle, "current_status"),
                "vehicle_ts": _optional_value(vehicle, "timestamp"),
                "occupancy_status": _optional_value(vehicle, "occupancy_status"),
                "occupancy_percentage": _optional_value(vehicle, "occupancy_percentage"),
            }
        )
    return rows


def parse_trip_updates(payload: bytes) -> list[dict[str, object]]:
    """Parse trip updates into one row per predicted stop event."""

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    rows: list[dict[str, object]] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        trip = update.trip
        for stop_update in update.stop_time_update:
            arrival = stop_update.arrival
            departure = stop_update.departure
            rows.append(
                {
                    "entity_id": str(entity.id),
                    "feed_header_ts": int(feed.header.timestamp),
                    "trip_update_ts": _optional_value(update, "timestamp"),
                    "trip_id": str(trip.trip_id) if trip.trip_id else None,
                    "route_id": str(trip.route_id) if trip.route_id else None,
                    "direction_id": _optional_value(trip, "direction_id"),
                    "start_date": str(trip.start_date) if trip.start_date else None,
                    "start_time": str(trip.start_time) if trip.start_time else None,
                    "vehicle_id": str(update.vehicle.id) if update.vehicle.id else None,
                    "stop_sequence": _optional_value(stop_update, "stop_sequence"),
                    "stop_id": str(stop_update.stop_id) if stop_update.stop_id else None,
                    "arrival_delay": _optional_value(arrival, "delay"),
                    "arrival_time": _optional_value(arrival, "time"),
                    "departure_delay": _optional_value(departure, "delay"),
                    "departure_time": _optional_value(departure, "time"),
                    "schedule_relationship": _optional_value(stop_update, "schedule_relationship"),
                }
            )
    return rows


def parse_alerts(payload: bytes) -> list[dict[str, object]]:
    """Parse service alerts into one row per alert entity."""

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    rows: list[dict[str, object]] = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        rows.append(
            {
                "entity_id": str(entity.id),
                "feed_header_ts": int(feed.header.timestamp),
                "cause": _optional_value(alert, "cause"),
                "effect": _optional_value(alert, "effect"),
                "severity_level": _optional_value(alert, "severity_level"),
                "header_text": _localized_text(alert.header_text),
                "description_text": _localized_text(alert.description_text),
                "url": _localized_text(alert.url),
                "active_periods": [
                    {
                        "start": _optional_value(period, "start"),
                        "end": _optional_value(period, "end"),
                    }
                    for period in alert.active_period
                ],
                "informed_entities": [
                    {
                        "agency_id": str(selector.agency_id) if selector.agency_id else None,
                        "route_id": str(selector.route_id) if selector.route_id else None,
                        "route_type": _optional_value(selector, "route_type"),
                        "stop_id": str(selector.stop_id) if selector.stop_id else None,
                    }
                    for selector in alert.informed_entity
                ],
            }
        )
    return rows