"""Silver incidents: one row per Seattle Fire 911 dispatch, from the files landed every 2 hours.

Each fetch asks for the last 30 hours, so files overlap: the same dispatch appears in many files.
New files are parsed, repeats inside the batch are dropped first (a MERGE alone only compares with
the table: the lesson of the 2026-10-22 weather incident), then merged by incident number.
"""

from typing import Any

from transit_reliability.bronze.schema import volume_path
from transit_reliability.incidents.parse import parse_incidents

INCIDENTS = "incidents"
DDL = (
    "incident_number string, type string, category string, address string, "
    "dispatched_ts timestamp, latitude double, longitude double, source_file string, "
    "pulled_at timestamp"
)
COLUMNS = [c.strip().split(" ")[0] for c in DDL.split(",")]


def build_incidents(spark: Any, prefix: str, landing_volume: str) -> None:
    import datetime as dt

    from pyspark.sql import functions as F

    root = volume_path(landing_volume, "incidents")
    target = f"workspace.{prefix}_silver.{INCIDENTS}"
    try:
        listing = spark.read.format("binaryFile").option("recursiveFileLookup", "true").load(root)
        paths = [r["path"] for r in listing.select("path").collect()]
    except Exception as error:  # noqa: BLE001 - nothing landed yet is not a failure of this step
        print(f"no incident files under {root} yet ({str(error)[:120]})")
        return
    done: set[str] = set()
    if spark.catalog.tableExists(target):
        done = {
            r["source_file"] for r in spark.table(target).select("source_file").distinct().collect()
        }
    new = [p for p in paths if p.split("/")[-1] not in done]
    print(f"incident files landed: {len(paths)}, new: {len(new)}")
    if not new:
        return
    rows: list[tuple[Any, ...]] = []
    for r in spark.read.format("binaryFile").load(new).select("path", "content").collect():
        name = r["path"].split("/")[-1]
        pulled = dt.datetime.fromtimestamp(int(name.split("-")[0]) / 1000, dt.UTC)
        text = bytes(r["content"]).decode("utf-8")
        rows += [tuple(x[c] for c in COLUMNS) for x in parse_incidents(text, name, pulled)]
    frame = (
        spark.createDataFrame(rows, DDL)
        .where(F.col("incident_number").isNotNull())
        .dropDuplicates(["incident_number"])
    )
    if not spark.catalog.tableExists(target):
        frame.write.format("delta").mode("append").saveAsTable(target)
    else:
        frame.createOrReplaceTempView("incoming_incidents")
        spark.sql(
            f"MERGE INTO {target} t USING incoming_incidents s "
            "ON t.incident_number = s.incident_number WHEN NOT MATCHED THEN INSERT *"
        )
    print(f"{target}: {spark.table(target).count()} incidents ({len(rows)} rows parsed this run)")
