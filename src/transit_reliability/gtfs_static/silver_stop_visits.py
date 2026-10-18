"""Silver stop visits: one row per scheduled stop of a trip with when the bus reached and left it.

Grain: (service_date, trip_id, stop_sequence). Built from silver vehicle positions (fixes), the
route shapes, and the expanded schedule. The inference is the pure code in `arrivals/infer.py`;
this module only gathers each trip's inputs, applies it, and writes the result.

Trips are processed shape by shape (a cogroup of trips and their route shape), so each shape's
points are loaded once however many trips use it.
"""

import datetime as dt
from typing import Any

from transit_reliability.arrivals.trip_rows import stop_visit_rows

TABLE = "stop_visits"
# Service dates this recent are rebuilt each run: their trips can still be running, and more
# fixes may have arrived. Older dates are built once.
RECOMPUTE_DAYS = 2

OUT_DDL = (
    "trip_id string, route_id string, direction_id long, stop_sequence long, stop_id string, "
    "timepoint long, sched_arr double, sched_dep double, "
    "arr_lo double, arr_hi double, arr_est double, arr_method string, "
    "dep_lo double, dep_hi double, dep_est double, dep_method string, "
    "arr_delay_s double, dep_delay_s double, advance_ts double, dwell_first double, "
    "dwell_last double, n_fixes long"
)
INT_COLUMNS = ("direction_id", "stop_sequence", "timepoint", "n_fixes")


def _num(value: Any) -> float | None:
    """A float, or None for null and NaN."""

    return None if value is None or value != value else float(value)


def _int(value: Any) -> int | None:
    return None if value is None or value != value else int(value)


def _infer_group(trips: Any, shapes: Any) -> Any:
    """cogroup function: all trips of one shape, plus that shape's points (one row)."""

    import pandas as pd

    points: list[tuple[float, float, float]] = []
    if len(shapes):
        points = [
            (float(p["lat"]), float(p["lon"]), float(p["dist"])) for p in shapes.iloc[0]["points"]
        ]
    out: list[dict[str, Any]] = []
    for trip in trips.itertuples(index=False):
        fixes = [
            (
                float(f["ts"]),
                float(f["lat"]),
                float(f["lon"]),
                _int(f["next_seq"]),
                bool(f["stopped"]),
            )
            for f in (trip.fixes if trip.fixes is not None else [])
        ]
        stops = [
            (int(s["stop_sequence"]), str(s["stop_id"]), float(s["dist"]),
             _num(s["sched_arr"]), _num(s["sched_dep"]), _int(s["timepoint"]))
            for s in trip.stops
        ]  # fmt: skip
        for row in stop_visit_rows(points, fixes, stops):
            out.append(
                {"trip_id": trip.trip_id, "route_id": trip.route_id,
                 "direction_id": _int(trip.direction_id), **row}
            )  # fmt: skip
    columns = [c.split(" ")[0] for c in OUT_DDL.split(", ")]
    frame = pd.DataFrame(out, columns=columns)
    for name in INT_COLUMNS:
        frame[name] = pd.array(frame[name].tolist(), dtype="Int64")
    return frame


def _ts(column: str) -> Any:
    """Epoch seconds in `column` as a timestamp column of the same name."""

    from pyspark.sql import functions as F

    return F.timestamp_seconds(F.col(column)).alias(column)


def _build_day(spark: Any, prefix: str, day: dt.date) -> int:
    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    stamp = F.lit(day.isoformat()).cast("date")
    sched = spark.table(f"{silver}.scheduled_stop_times").where(F.col("service_date") == stamp)
    dists = spark.table(f"{silver}.gtfs_stop_times").select(
        "feed_version", "trip_id", "stop_sequence", F.col("shape_dist_traveled").alias("dist")
    )
    shape_ids = spark.table(f"{silver}.gtfs_trips").select("feed_version", "trip_id", "shape_id")
    trip_stops = (
        sched.join(dists, ["feed_version", "trip_id", "stop_sequence"])
        .join(shape_ids, ["feed_version", "trip_id"])
        .groupBy("feed_version", "trip_id", "route_id", "direction_id", "shape_id")
        .agg(
            F.sort_array(
                F.collect_list(
                    F.struct(
                        "stop_sequence",
                        "stop_id",
                        "dist",
                        F.unix_timestamp("scheduled_arrival_ts").cast("double").alias("sched_arr"),
                        F.unix_timestamp("scheduled_departure_ts")
                        .cast("double")
                        .alias("sched_dep"),
                        "timepoint",
                    )
                )
            ).alias("stops")
        )
    )
    fixes = (
        spark.table(f"{silver}.vehicle_positions")
        .where(F.col("service_date") == stamp)
        .groupBy("trip_id")
        .agg(
            F.sort_array(
                F.collect_list(
                    F.struct(
                        F.unix_timestamp("vehicle_ts").cast("double").alias("ts"),
                        F.col("latitude").alias("lat"),
                        F.col("longitude").alias("lon"),
                        F.col("current_stop_sequence").alias("next_seq"),
                        (F.col("current_status") == 1).alias("stopped"),
                    )
                )
            ).alias("fixes")
        )
    )
    trips = trip_stops.join(fixes, "trip_id", "left")
    feeds = [r["feed_version"] for r in trip_stops.select("feed_version").distinct().collect()]
    shapes = (
        spark.table(f"{silver}.gtfs_shapes")
        .where(F.col("feed_version").isin(feeds))
        .join(trips.select("shape_id").distinct(), "shape_id", "left_semi")
        .groupBy("shape_id")
        .agg(
            F.sort_array(
                F.collect_list(
                    F.struct(
                        "shape_pt_sequence",
                        F.col("shape_pt_lat").alias("lat"),
                        F.col("shape_pt_lon").alias("lon"),
                        F.col("shape_dist_traveled").alias("dist"),
                    )
                )
            ).alias("points")
        )
    )
    result = (
        trips.groupBy("shape_id")
        .cogroup(shapes.groupBy("shape_id"))
        .applyInPandas(_infer_group, OUT_DDL)
    )
    final = result.select(
        stamp.alias("service_date"),
        "trip_id", "route_id", "direction_id", "stop_sequence", "stop_id", "timepoint",
        _ts("sched_arr"), _ts("sched_dep"),
        _ts("arr_lo"), _ts("arr_hi"), _ts("arr_est"), "arr_method",
        _ts("dep_lo"), _ts("dep_hi"), _ts("dep_est"), "dep_method",
        "arr_delay_s", "dep_delay_s",
        _ts("advance_ts"), _ts("dwell_first"), _ts("dwell_last"), "n_fixes",
    )  # fmt: skip
    target = f"{silver}.{TABLE}"
    if spark.catalog.tableExists(target):
        spark.sql(f"DELETE FROM {target} WHERE service_date = DATE'{day.isoformat()}'")
    final.write.format("delta").mode("append").saveAsTable(target)
    return int(spark.table(target).where(F.col("service_date") == stamp).count())


def build_stop_visits(spark: Any, prefix: str, rebuild_from: dt.date | None = None) -> None:
    """Build stop visits for service dates that have fixes and an expanded schedule."""

    silver = f"workspace.{prefix}_silver"
    with_fixes = {
        r["service_date"]
        for r in spark.table(f"{silver}.vehicle_positions")
        .select("service_date")
        .distinct()
        .collect()
    }
    scheduled = {
        r["service_date"]
        for r in spark.table(f"{silver}.scheduled_stop_times")
        .select("service_date")
        .distinct()
        .collect()
    }
    target = f"{silver}.{TABLE}"
    done = (
        {r["service_date"] for r in spark.table(target).select("service_date").distinct().collect()}
        if spark.catalog.tableExists(target)
        else set()
    )
    today = dt.datetime.now(dt.UTC).date()
    recent = today - dt.timedelta(days=RECOMPUTE_DAYS)
    for day in sorted(with_fixes & scheduled):
        if day in done and day < recent and (rebuild_from is None or day < rebuild_from):
            continue
        rows = _build_day(spark, prefix, day)
        print(f"stop visits {day}: {rows} rows")
