"""Databricks Auto Loader jobs for the GTFS-Realtime bronze layer."""

import json
from collections.abc import Iterator
from typing import Any, cast

from transit_reliability.bronze.schema import (
    SOURCE_TO_COLUMNS,
    missing_columns,
    table_name,
    volume_path,
)
from transit_reliability.gtfs_rt.parse import (
    parse_alerts,
    parse_trip_updates,
    parse_vehicle_positions,
)

PARSER_BY_SOURCE = {
    "vehicle_positions": parse_vehicle_positions,
    "trip_updates": parse_trip_updates,
    "alerts": parse_alerts,
}

STRING_COLUMNS = frozenset(
    {
        "entity_id",
        "vehicle_id",
        "vehicle_label",
        "trip_id",
        "route_id",
        "start_date",
        "start_time",
        "stop_id",
        "header_text",
        "description_text",
        "url",
        "source_file",
    }
)
DOUBLE_COLUMNS = frozenset({"latitude", "longitude", "bearing", "speed"})
ARRAY_COLUMNS = frozenset({"active_periods", "informed_entities"})
# ingested_at is stamped by Spark after parsing, so the parser output omits it.
# Files per micro-batch. A trip-update file is about 1.2 MB and 33K rows, so an unbounded
# first batch over a backlog exhausts the Python worker's memory.
MAX_FILES_PER_TRIGGER = {"vehicle_positions": 1000, "trip_updates": 50, "alerts": 1000}
PARSED_COLUMNS: dict[str, tuple[str, ...]] = {
    source: tuple(name for name in columns if name != "ingested_at")
    for source, columns in SOURCE_TO_COLUMNS.items()
}


def column_ddl_types(source: str) -> list[tuple[str, str]]:
    """Spark SQL type of every bronze column in contract order (used to evolve tables)."""

    types = []
    for name in SOURCE_TO_COLUMNS[source]:
        if name == "ingested_at":
            ddl = "timestamp"
        elif name in ARRAY_COLUMNS:
            ddl = "array<string>"
        elif name in STRING_COLUMNS:
            ddl = "string"
        elif name in DOUBLE_COLUMNS:
            ddl = "double"
        else:
            ddl = "bigint"
        types.append((name, ddl))
    return types


def ensure_table_columns(spark: Any, source: str, target: str) -> None:
    """Add contract columns an older bronze table lacks. Old rows get NULL for them."""

    if not spark.catalog.tableExists(target):
        return
    for name, ddl in missing_columns(spark.table(target).columns, column_ddl_types(source)):
        spark.sql(f"ALTER TABLE {target} ADD COLUMNS ({name} {ddl})")
        print(f"added column {name} {ddl} to {target}")


def _output_schema(source: str) -> Any:
    """Build the Spark schema of the parsed (pre-ingested_at) rows."""

    from pyspark.sql.types import (
        ArrayType,
        DoubleType,
        LongType,
        StringType,
        StructField,
        StructType,
    )

    fields = []
    for name in PARSED_COLUMNS[source]:
        if name in ARRAY_COLUMNS:
            field_type: Any = ArrayType(StringType())
        elif name in STRING_COLUMNS:
            field_type = StringType()
        elif name in DOUBLE_COLUMNS:
            field_type = DoubleType()
        else:
            field_type = LongType()
        fields.append(StructField(name, field_type, True))
    return StructType(fields)


def parse_record(source: str, path: str, content: bytes) -> list[dict[str, object]]:
    """Parse one landed file into bronze rows (without ingested_at)."""

    rows = PARSER_BY_SOURCE[source](content)
    for row in rows:
        row["source_file"] = path
        if source == "alerts":
            row["active_periods"] = [
                json.dumps(value) for value in cast(list[object], row["active_periods"])
            ]
            row["informed_entities"] = [
                json.dumps(value) for value in cast(list[object], row["informed_entities"])
            ]
    return rows


def _parse_partition(source: str, batches: Iterator[Any]) -> Iterator[Any]:
    """Parse binaryFile rows in a pandas batch iterator (mapInPandas)."""

    import pandas as pd

    columns = PARSED_COLUMNS[source]
    for batch in batches:
        # One output frame per file keeps peak memory to a single file's rows.
        for path, content in zip(batch["path"], batch["content"], strict=True):
            rows = parse_record(source, str(path), bytes(content))
            data: dict[str, Any] = {}
            for name in columns:
                values = [row.get(name) for row in rows]
                if name in STRING_COLUMNS or name in ARRAY_COLUMNS:
                    data[name] = pd.Series(values, dtype="object")
                elif name in DOUBLE_COLUMNS:
                    data[name] = pd.Series(values, dtype="float64")
                else:
                    # Nullable Int64 keeps absent optional ints as NULL, not NaN floats.
                    data[name] = pd.array(values, dtype="Int64")
            yield pd.DataFrame(data, columns=list(columns))


def run_source(
    spark: Any,
    source: str,
    landing_volume: str,
    state_volume: str,
    schema_prefix: str,
) -> None:
    """Run one availableNow Auto Loader stream into a bronze Delta table."""

    from pyspark.sql import functions as F

    if source not in PARSER_BY_SOURCE:
        raise ValueError(f"Unsupported bronze source: {source}")
    source_path = volume_path(landing_volume, source)
    # Per-prefix: dev and prod must not share a checkpoint or they steal each other's files.
    checkpoint = volume_path(state_volume, "checkpoints", f"{schema_prefix}_bronze_{source}")
    target = table_name(schema_prefix, source)
    schema = _output_schema(source)
    ensure_table_columns(spark, source, target)
    stream = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "binaryFile")
        .option("cloudFiles.includeExistingFiles", "true")
        .option("cloudFiles.maxFilesPerTrigger", MAX_FILES_PER_TRIGGER[source])
        .load(source_path)
        .select("path", "content")
    )

    def write_batch(batch_df: Any, batch_id: int) -> None:
        parsed = batch_df.mapInPandas(lambda it: _parse_partition(source, it), schema)
        stamped = parsed.withColumn("ingested_at", F.current_timestamp())
        if spark.catalog.tableExists(target):
            # Write in the table's own column order, so columns added later (at the end of the
            # table) cannot be matched by position to the wrong data.
            stamped = stamped.select(*spark.table(target).columns)
        (
            stamped.write.format("delta")
            # txnAppId/txnVersion make a retried micro-batch an idempotent no-op.
            .option("txnAppId", f"bronze_{source}")
            .option("txnVersion", batch_id)
            .mode("append")
            .saveAsTable(target)
        )

    query = (
        stream.writeStream.foreachBatch(write_batch)
        .option("checkpointLocation", checkpoint)
        .trigger(availableNow=True)
        .start()
    )
    query.awaitTermination()
