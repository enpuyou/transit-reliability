"""Build the `<prefix>_public` tables and export them as one JSON file for the website.

The website never queries Databricks. A job (this module) turns the pipeline's state into a small
set of aggregate tables, exports every table of `<prefix>_public` into `site.json` inside the state
volume, and a GitHub workflow copies that one file to the site. One broken section does not block
the rest: its error is recorded in the file (and shown on the page) and the task fails afterwards.
"""

import datetime as dt
import decimal
import json
import os
from dataclasses import asdict
from typing import Any

from transit_reliability.bronze.schema import volume_path
from transit_reliability.public.catalog import GLOSSARY, LAYERS, TABLES
from transit_reliability.public.queries import (
    heavy_queries,
    live_queries,
    public_schema,
    sample_queries,
)

ATTRIBUTION = (
    "Transit scheduling, geographic, and real-time data provided by permission of King County."
)
SITE_FILE = ("public", "site.json")
PIPELINE_DDL = (
    "layer string, name string, purpose string, grain string, built_by string, "
    "row_count bigint, newest string, error string"
)
SAMPLES_DDL = "table_name string, layer string, sample_json string"


def plain(value: Any) -> Any:
    """A JSON-friendly copy of one Spark value."""

    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value


def build_payload(
    tables: dict[str, list[dict[str, Any]]],
    errors: dict[str, str],
    prefix: str,
    now: dt.datetime,
) -> dict[str, Any]:
    return {
        "generated_at": now.isoformat(),
        "schema_prefix": prefix,
        "attribution": ATTRIBUTION,
        "layers": [asdict(layer) for layer in LAYERS],
        "glossary": GLOSSARY,
        "tables": tables,
        "errors": errors,
    }


def _create(spark: Any, pub: str, name: str, sql: str, errors: dict[str, str]) -> None:
    try:
        spark.sql(f"CREATE OR REPLACE TABLE {pub}.{name} AS {sql}")
        print(f"built {pub}.{name}")
    except Exception as error:  # noqa: BLE001 - recorded, shown on the page, fails the task later
        errors[name] = str(error)[:300]
        print(f"FAILED {name}: {errors[name]}")


def _pipeline_tables(spark: Any, prefix: str, pub: str, errors: dict[str, str]) -> None:
    rows: list[tuple[Any, ...]] = []
    for t in TABLES:
        fq = f"workspace.{prefix}_{t.layer}.{t.name}"
        newest_expr = t.newest_expr or "NULL"
        try:
            r = spark.sql(
                f"SELECT count(*) AS n, CAST({newest_expr} AS STRING) AS newest FROM {fq}"
            ).collect()[0]
            rows.append(
                (t.layer, t.name, t.purpose, t.grain, t.built_by, int(r["n"]), r["newest"], None)
            )
        except Exception as error:  # noqa: BLE001
            rows.append(
                (t.layer, t.name, t.purpose, t.grain, t.built_by, None, None, str(error)[:200])
            )
            errors[f"pipeline_tables.{t.layer}.{t.name}"] = str(error)[:200]
    spark.createDataFrame(rows, PIPELINE_DDL).write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(f"{pub}.pipeline_tables")
    print(f"built {pub}.pipeline_tables ({len(rows)} tables)")


def _samples(spark: Any, prefix: str, pub: str, errors: dict[str, str]) -> None:
    rows: list[tuple[Any, ...]] = []
    for name, (layer, sql) in sample_queries(prefix).items():
        try:
            found = spark.sql(sql).collect()
            rows.append((name, layer, found[0][0] if found else None))
        except Exception as error:  # noqa: BLE001
            rows.append((name, layer, None))
            errors[f"samples.{name}"] = str(error)[:200]
    spark.createDataFrame(rows, SAMPLES_DDL).write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(f"{pub}.samples")
    print(f"built {pub}.samples ({len(rows)} tables)")


def export_site(
    spark: Any, prefix: str, state_volume: str, errors: dict[str, str]
) -> dict[str, int]:
    pub = public_schema(prefix)
    tables: dict[str, list[dict[str, Any]]] = {}
    for entry in spark.sql(f"SHOW TABLES IN {pub}").collect():
        name = entry["tableName"]
        tables[name] = [
            plain(r.asDict(recursive=True)) for r in spark.table(f"{pub}.{name}").collect()
        ]
    payload = build_payload(tables, errors, prefix, dt.datetime.now(dt.UTC))
    path = volume_path(state_volume, *SITE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), default=str)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote {path}: {len(text):,} bytes, {len(tables)} tables")
    return {name: len(rows) for name, rows in tables.items()}


def is_publish_hour(now: dt.datetime, every_hours: int) -> bool:
    """True when the website tables should be refreshed in this UTC hour.

    The monitor job runs every hour but the tables only need refreshing every `every_hours`
    (a job run costs compute). Hours 0, 3, 6, ... when `every_hours` is 3.
    """

    return every_hours <= 1 or now.hour % every_hours == 0


def publish_site(spark: Any, prefix: str, state_volume: str, parts: str) -> None:
    """parts='live' refreshes the hourly sections; 'all' also the heavy daily ones."""

    pub = public_schema(prefix)
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {pub}")
    errors: dict[str, str] = {}
    for name, sql in live_queries(prefix).items():
        _create(spark, pub, name, sql, errors)
    _pipeline_tables(spark, prefix, pub, errors)
    if parts == "all":
        for name, sql in heavy_queries(prefix).items():
            _create(spark, pub, name, sql, errors)
        _samples(spark, prefix, pub, errors)
    sizes = export_site(spark, prefix, state_volume, errors)
    print("rows per table:", sizes)
    if errors:
        raise RuntimeError(f"{len(errors)} public section(s) failed: " + "; ".join(errors))
