"""Silver vehicle positions: deduped GPS fixes matched to the schedule, bad rows quarantined.

Grain of `vehicle_positions`: one distinct fix of one bus, keyed by (vehicle_id, vehicle_ts).
Bronze keeps every snapshot, so the same fix appears in many snapshots; silver keeps it once.
Rows failing the rules go to `quarantine_vehicle_positions` with a reason. Rows whose trip is
not in the schedule are kept with `matched_trip = false` so the match rate stays measurable.
"""

from typing import Any

from transit_reliability.quality.rules import (
    KING_COUNTY_BOX,
    MAX_FUTURE_SECONDS,
    MAX_STALE_SECONDS,
)

SILVER_TABLE = "vehicle_positions"
QUARANTINE_TABLE = "quarantine_vehicle_positions"
# Bronze batches are stamped when they start writing and become visible when they commit, so
# a run re-reads a little before its watermark. The MERGE below makes the overlap harmless.
LOOKBACK = "INTERVAL 1 HOUR"


def reason_column() -> Any:
    """Spark expression equal to `quality.rules.quarantine_reason` (same order and limits)."""

    from pyspark.sql import functions as F

    lat_min, lat_max, lon_min, lon_max = KING_COUNTY_BOX
    ts, feed = F.col("vehicle_ts"), F.col("feed_header_ts")
    lat, lon = F.col("latitude"), F.col("longitude")
    return (
        F.when(ts.isNull() | (ts == 0), "NO_VEHICLE_TS")
        .when(lat.isNull() | lon.isNull() | (lat == 0) | (lon == 0), "ZERO_COORDINATES")
        .when(
            ~(lat.between(lat_min, lat_max) & lon.between(lon_min, lon_max)),
            "OUT_OF_BOUNDS",
        )
        .when(ts > feed + MAX_FUTURE_SECONDS, "TS_IN_FUTURE")
        .when(ts < feed - MAX_STALE_SECONDS, "TS_STALE")
        .when(F.col("vehicle_id").isNull() | (F.col("vehicle_id") == ""), "MISSING_VEHICLE_ID")
        .when(F.col("trip_id").isNull() | (F.col("trip_id") == ""), "MISSING_TRIP_ID")
    )


def _exists(spark: Any, table: str) -> bool:
    return bool(spark.catalog.tableExists(table))


def _merge(spark: Any, df: Any, target: str, keys: tuple[str, ...]) -> None:
    """Insert rows whose key is not in the target yet (idempotent under overlap)."""

    if not _exists(spark, target):
        df.write.format("delta").mode("append").saveAsTable(target)
        return
    df.createOrReplaceTempView("incoming")
    on = " AND ".join(f"t.{k} <=> s.{k}" for k in keys)
    spark.sql(f"MERGE INTO {target} t USING incoming s ON {on} WHEN NOT MATCHED THEN INSERT *")


def _repair_unknown_matches(spark: Any, silver: str, scheduled: str) -> None:
    """Fill `matched_trip` for rows stored while their service date had no scheduled trips yet.

    Only rows that are NULL are touched, and only once their date is known, so this is cheap and
    safe to run every time. DISTINCT keeps the MERGE source unique per target row.
    """

    if not (_exists(spark, silver) and _exists(spark, scheduled)):
        return
    spark.sql(
        f"""
        MERGE INTO {silver} t USING (
          SELECT v.vehicle_id, v.vehicle_ts, (st.trip_id IS NOT NULL) AS matched
          FROM {silver} v
          JOIN (SELECT DISTINCT service_date FROM {scheduled}) d ON d.service_date = v.service_date
          LEFT JOIN (SELECT DISTINCT service_date, trip_id FROM {scheduled}) st
            ON st.service_date = v.service_date AND st.trip_id = v.trip_id
          WHERE v.matched_trip IS NULL
        ) s ON t.vehicle_id = s.vehicle_id AND t.vehicle_ts = s.vehicle_ts
        WHEN MATCHED THEN UPDATE SET t.matched_trip = s.matched
        """
    )


def _cutoff(spark: Any, silver: str, quarantine: str, since: str | None) -> str | None:
    """Timestamp text: bronze rows ingested after it are read. None means read everything."""

    if since is not None:
        return since
    if not (_exists(spark, silver) and _exists(spark, quarantine)):
        return None
    row = spark.sql(
        f"SELECT date_format(max(t) - {LOOKBACK}, 'yyyy-MM-dd HH:mm:ss.SSSSSS') AS c FROM ("
        f"SELECT max(bronze_ingested_at) t FROM {silver} "
        f"UNION ALL SELECT max(bronze_ingested_at) FROM {quarantine})"
    ).collect()[0]
    return None if row["c"] is None else str(row["c"])


def build_vehicle_positions(spark: Any, prefix: str, since: str | None = None) -> None:
    """Build silver vehicle positions. `since` (a timestamp text) forces re-reading bronze from
    then; the MERGE only inserts what is missing, so a backfill is safe to repeat."""

    from pyspark.sql import Window
    from pyspark.sql import functions as F

    bronze = f"workspace.{prefix}_bronze.vehicle_positions"
    silver = f"workspace.{prefix}_silver.{SILVER_TABLE}"
    quarantine = f"workspace.{prefix}_silver.{QUARANTINE_TABLE}"
    scheduled = f"workspace.{prefix}_silver.scheduled_trips"

    _repair_unknown_matches(spark, silver, scheduled)

    rows = spark.table(bronze).where(~F.col("source_file").contains("smoke"))
    # Computed ONCE, now, before anything is written. A scalar subquery inside the filter is
    # evaluated lazily when each write runs, so after the quarantine write had advanced the
    # watermark the silver write read only the last hour (the bug of 2026-10-16 to 18).
    cutoff = _cutoff(spark, silver, quarantine, since)
    if cutoff is not None:
        rows = rows.where(F.expr(f"ingested_at > TIMESTAMP'{cutoff}'"))
    rows = rows.withColumn("reason", reason_column())

    bad = rows.where(F.col("reason").isNotNull()).select(
        F.sha2(
            F.concat_ws(
                "|",
                "source_file",
                "entity_id",
                "vehicle_id",
                "vehicle_ts",
                "trip_id",
                "latitude",
                "longitude",
            ),
            256,
        ).alias("row_key"),
        "reason",
        "vehicle_id",
        "vehicle_ts",
        "feed_header_ts",
        "trip_id",
        "route_id",
        "latitude",
        "longitude",
        "source_file",
        F.col("ingested_at").alias("bronze_ingested_at"),
    )

    window = Window.partitionBy("vehicle_id", "vehicle_ts").orderBy(
        "feed_header_ts", "source_file", "entity_id"
    )
    good = (
        rows.where(F.col("reason").isNull())
        .withColumn("rn", F.row_number().over(window))
        .where(F.col("rn") == 1)
    )
    sched_trips = (
        spark.table(scheduled)
        .select("service_date", "trip_id")
        .distinct()
        .withColumn("on_schedule", F.lit(True))
    )
    sched_days = (
        spark.table(scheduled)
        .select("service_date")
        .distinct()
        .withColumn("day_known", F.lit(True))
    )
    fixes = (
        good.withColumn("service_date", F.to_date("start_date", "yyyyMMdd"))
        .join(sched_trips, ["service_date", "trip_id"], "left")
        .join(sched_days, "service_date", "left")
        .select(
            "vehicle_id",
            F.timestamp_seconds("vehicle_ts").alias("vehicle_ts"),
            "service_date",
            "trip_id",
            "route_id",
            "direction_id",
            "start_time",
            "latitude",
            "longitude",
            "current_stop_sequence",
            "stop_id",
            "current_status",
            "occupancy_status",
            F.timestamp_seconds("feed_header_ts").alias("first_feed_ts"),
            # NULL when that service date has not been expanded, so "unknown" is not "false".
            F.when(F.col("day_known").isNull(), F.lit(None).cast("boolean"))
            .otherwise(F.coalesce(F.col("on_schedule"), F.lit(False)))
            .alias("matched_trip"),
            "source_file",
            F.col("ingested_at").alias("bronze_ingested_at"),
        )
    )

    _merge(spark, bad, quarantine, ("row_key",))
    _merge(spark, fixes, silver, ("vehicle_id", "vehicle_ts"))
    # Integrity: everything this run meant to write must now be there. Fail loudly otherwise.
    missing_fixes = fixes.join(
        spark.table(silver), ["vehicle_id", "vehicle_ts"], "left_anti"
    ).count()
    missing_bad = bad.join(spark.table(quarantine), ["row_key"], "left_anti").count()
    if missing_fixes or missing_bad:
        raise RuntimeError(
            "silver is missing rows it should have written: "
            f"{missing_fixes} fixes, {missing_bad} quarantined"
        )
    total = spark.table(silver).count()
    bad_total = spark.table(quarantine).count()
    print(f"silver vehicle_positions: {total} rows; quarantine: {bad_total} rows")
    for r in spark.table(quarantine).groupBy("reason").count().orderBy("reason").collect():
        print(f"  quarantine {r['reason']}: {r['count']}")
