"""Silver trip-update snapshots: one row per run of an unchanged prediction.

Bronze keeps one row per stop prediction per snapshot (about 35K a minute); about two thirds
repeat the previous snapshot. Silver keeps one row per *run*: a prediction for one stop of one
trip that stayed the same over consecutive snapshots, with when it was first and last seen.

The rules (what counts as a change, how a run continues across batches, how overlapping reads
are ignored) are specified and tested in `silver_logic/runs.py`; this module is the Spark
version of the same thing and is verified against an independent SQL count (decision 0010).
"""

from functools import reduce
from typing import Any

from transit_reliability.silver_logic.runs import MAX_GAP_SECONDS

SNAPSHOT_TABLE = "trip_update_snapshots"
KEY = ("trip_id", "service_date", "stop_sequence")
# A change in any of these starts a new run. trip_update_ts is excluded on purpose: it moves
# every update even when the prediction does not. vehicle_id is included, so the moment a bus
# is assigned to a trip shows up as a run boundary.
VALUES = (
    "arrival_time",
    "arrival_delay",
    "departure_time",
    "departure_delay",
    "stop_schedule_relationship",
    "trip_schedule_relationship",
    "vehicle_id",
)
CARRIED = ("route_id", "direction_id", "stop_id")
# The open run of a key is looked up this far back; it must be at least MAX_GAP_SECONDS so a run
# that could still continue is always found (a longer silence starts a new run anyway).
LOOKBACK_SECONDS = MAX_GAP_SECONDS
CHUNK_SECONDS = 3 * 3600  # event-time window per pass, so a backfill never sorts it all at once


def _same_key(left: Any, right_prefix: str) -> Any:
    """Null-safe equality of every key column against prefixed copies on the other side."""

    from pyspark.sql import functions as F

    return reduce(
        lambda a, b: a & b, [F.col(k).eqNullSafe(F.col(f"{right_prefix}{k}")) for k in KEY]
    )


def _fold_chunk(spark: Any, snaps: Any, target: str, cursor: int, upper: int) -> None:
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    key = list(KEY)
    chunk = snaps.where((F.col("ts") > cursor) & (F.col("ts") <= upper))
    # At most one row per (key, snapshot time): a feed version fetched twice must count once.
    one = Window.partitionBy(*key, "ts").orderBy("ingested_at", "source_file")
    chunk = chunk.withColumn("rn", F.row_number().over(one)).where("rn = 1").drop("rn")
    chunk = chunk.select(*key, *CARRIED, "ts", *VALUES, "source_file", "ingested_at").withColumns(
        {"first_ts": F.col("ts"), "weight": F.lit(1), "is_prior": F.lit(False)}
    )

    if spark.catalog.tableExists(target):
        recent = spark.table(target).where(
            F.col("last_seen") >= F.timestamp_seconds(F.lit(cursor - LOOKBACK_SECONDS))
        )
        keys = chunk.select(*[F.col(k).alias(f"c_{k}") for k in KEY]).distinct()
        cond_c = reduce(lambda a, b: a & b, [F.col(k).eqNullSafe(F.col(f"c_{k}")) for k in KEY])
        latest = Window.partitionBy(*key).orderBy(F.col("last_seen").desc())
        prior = (
            recent.join(keys, cond_c, "left_semi")
            .withColumn("rn", F.row_number().over(latest))
            .where("rn = 1")
            .drop("rn")
        )
        # The open run enters the fold as one pseudo-snapshot at its last_seen time.
        pseudo = prior.select(
            *key,
            *CARRIED,
            F.unix_timestamp("last_seen").alias("ts"),
            F.unix_timestamp("arrival_time").alias("arrival_time"),
            "arrival_delay",
            F.unix_timestamp("departure_time").alias("departure_time"),
            "departure_delay",
            "stop_schedule_relationship",
            "trip_schedule_relationship",
            "vehicle_id",
            F.lit(None).cast("string").alias("source_file"),
            F.col("bronze_ingested_at").alias("ingested_at"),
            F.unix_timestamp("first_seen").alias("first_ts"),
            F.col("snapshots").alias("weight"),
            F.lit(True).alias("is_prior"),
        )
        # Drop snapshots already folded into the open run (the lookback overlap).
        cutoff = prior.select(
            *[F.col(k).alias(f"p_{k}") for k in KEY],
            F.unix_timestamp("last_seen").alias("prior_last"),
        )
        chunk = (
            chunk.join(cutoff, _same_key(chunk, "p_"), "left")
            .where(F.col("prior_last").isNull() | (F.col("ts") > F.col("prior_last")))
            .drop("prior_last", *[f"p_{k}" for k in KEY])
        )
        combined = pseudo.unionByName(chunk)
    else:
        combined = chunk

    ordered = Window.partitionBy(*key).orderBy("ts", F.col("is_prior").desc())
    same = reduce(lambda a, b: a & b, [F.col(c).eqNullSafe(F.lag(c).over(ordered)) for c in VALUES])
    previous = F.lag("ts").over(ordered)
    combined = combined.withColumn(
        "chg",
        F.when(previous.isNull(), 1)
        .when(F.col("ts") - previous > MAX_GAP_SECONDS, 1)  # a long silence ends the run
        .when(same, 0)
        .otherwise(1),
    ).withColumn(
        "run_id",
        F.sum("chg").over(ordered.rowsBetween(Window.unboundedPreceding, Window.currentRow)),
    )
    runs = (
        combined.groupBy(*key, "run_id")
        .agg(
            *[F.max(c).alias(c) for c in (*CARRIED, *VALUES)],
            F.min("first_ts").alias("first_ts"),
            F.max("ts").alias("last_ts"),
            F.sum("weight").alias("snapshots"),
            F.max("ingested_at").alias("bronze_ingested_at"),
            F.sum(F.when(F.col("is_prior"), 0).otherwise(1)).alias("new_rows"),
        )
        .where(F.col("new_rows") > 0)  # a prior run nothing was added to needs no write
        .select(
            *key,
            *CARRIED,
            F.timestamp_seconds("arrival_time").alias("arrival_time"),
            "arrival_delay",
            F.timestamp_seconds("departure_time").alias("departure_time"),
            "departure_delay",
            "stop_schedule_relationship",
            "trip_schedule_relationship",
            "vehicle_id",
            F.timestamp_seconds("first_ts").alias("first_seen"),
            F.timestamp_seconds("last_ts").alias("last_seen"),
            F.col("snapshots").cast("bigint").alias("snapshots"),
            "bronze_ingested_at",
        )
    )
    if not spark.catalog.tableExists(target):
        runs.write.format("delta").mode("append").saveAsTable(target)
        return
    runs.createOrReplaceTempView("run_updates")
    spark.sql(
        f"""
        MERGE INTO {target} t USING run_updates s
        ON t.trip_id = s.trip_id AND t.service_date = s.service_date
           AND t.stop_sequence <=> s.stop_sequence AND t.first_seen = s.first_seen
        WHEN MATCHED THEN UPDATE SET t.last_seen = s.last_seen, t.snapshots = s.snapshots,
                                     t.bronze_ingested_at = s.bronze_ingested_at
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def build_trip_update_snapshots(
    spark: Any,
    prefix: str,
    chunk_seconds: int = CHUNK_SECONDS,
    until_ts: int | None = None,
) -> None:
    """Fold bronze snapshots into runs. `until_ts` stops early (tests and staged backfills)."""

    from pyspark.sql import functions as F

    bronze = spark.table(f"workspace.{prefix}_bronze.trip_updates")
    target = f"workspace.{prefix}_silver.{SNAPSHOT_TABLE}"
    # Older bronze tables (before decision 0009) have no trip-level column; NULL means SCHEDULED.
    if "trip_schedule_relationship" in bronze.columns:
        trip_rel = F.coalesce(F.col("trip_schedule_relationship"), F.lit(0))
    else:
        trip_rel = F.lit(0)
    snaps = (
        bronze.where(~F.col("source_file").contains("smoke"))
        .where(F.col("trip_id").isNotNull())
        .where(F.col("start_date").rlike("^[0-9]{8}$"))
        .select(
            "trip_id",
            F.to_date("start_date", "yyyyMMdd").alias("service_date"),
            "stop_sequence",
            "route_id",
            "direction_id",
            "stop_id",
            F.col("feed_header_ts").alias("ts"),
            "arrival_time",
            "arrival_delay",
            "departure_time",
            "departure_delay",
            F.col("schedule_relationship").alias("stop_schedule_relationship"),
            trip_rel.cast("bigint").alias("trip_schedule_relationship"),
            "vehicle_id",
            "source_file",
            "ingested_at",
        )
    )
    bounds = snaps.agg(F.min("ts").alias("lo"), F.max("ts").alias("hi")).collect()[0]
    if bounds["lo"] is None:
        print("no trip updates yet")
        return
    hi = int(bounds["hi"]) if until_ts is None else min(int(bounds["hi"]), until_ts)
    if spark.catalog.tableExists(target):
        last = spark.table(target).agg(F.max(F.unix_timestamp("last_seen"))).collect()[0][0]
        cursor = int(last or bounds["lo"]) - LOOKBACK_SECONDS
    else:
        cursor = int(bounds["lo"]) - 1
    while cursor < hi:
        upper = min(cursor + chunk_seconds, hi)
        _fold_chunk(spark, snaps, target, cursor, upper)
        print(f"folded snapshots in ({cursor}, {upper}]")
        cursor = upper
    print(f"trip_update_snapshots rows: {spark.table(target).count()}")
