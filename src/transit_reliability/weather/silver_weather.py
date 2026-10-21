# ruff: noqa: E501  (SQL is kept on readable single lines)
"""Silver weather: observations parsed from the landed National Weather Service files, and an hourly summary.

`weather_observations`: grain (station, observed_ts), one row per station observation (about every 5
minutes), deduplicated because each hourly fetch overlaps the previous ones. `weather_hourly`: one row per
UTC hour summarising all stations, with the hour's class (`rain`, `fog` or `dry`, `parse.hour_class`) and
the Seattle date and hour used to join trips. Decision 0024.

Weather is a re-fetchable source (the service keeps about 7 days), so unlike the bus feeds this layer
parses the landed files directly instead of keeping a separate bronze table.
"""

from typing import Any

from transit_reliability.bronze.schema import volume_path
from transit_reliability.weather.parse import FOG_SHARE_FOR_FOG, WET_SHARE_FOR_RAIN, parse_file

OBSERVATIONS = "weather_observations"
HOURLY = "weather_hourly"
OBS_DDL = (
    "station string, observed_ts timestamp, temp_c double, wind_kmh double, gust_kmh double, "
    "visibility_m double, humidity_pct double, pressure_pa double, text string, precip_level int, "
    "fog boolean, codes string, source_file string, ingested_at timestamp"
)


def hourly_sql(prefix: str) -> str:
    obs = f"workspace.{prefix}_silver.{OBSERVATIONS}"
    return f"""
    SELECT date_trunc('HOUR', observed_ts) AS hour_start,
           to_date(from_utc_timestamp(date_trunc('HOUR', observed_ts), 'America/Los_Angeles')) AS pt_date,
           hour(from_utc_timestamp(date_trunc('HOUR', observed_ts), 'America/Los_Angeles')) AS pt_hour,
           count(*) AS n_obs, count(DISTINCT station) AS n_stations,
           avg(temp_c) AS temp_c_avg, avg(wind_kmh) AS wind_kmh_avg, max(gust_kmh) AS gust_kmh_max,
           min(visibility_m) AS visibility_m_min, avg(humidity_pct) AS humidity_pct_avg,
           avg(CASE WHEN precip_level > 0 THEN 1.0 ELSE 0.0 END) AS wet_share,
           max(precip_level) AS precip_level_max,
           avg(CASE WHEN fog THEN 1.0 ELSE 0.0 END) AS fog_share,
           CASE WHEN avg(CASE WHEN precip_level > 0 THEN 1.0 ELSE 0.0 END) >= {WET_SHARE_FOR_RAIN} THEN 'rain'
                WHEN avg(CASE WHEN fog THEN 1.0 ELSE 0.0 END) >= {FOG_SHARE_FOR_FOG} THEN 'fog' ELSE 'dry' END AS weather_class
    FROM {obs} GROUP BY date_trunc('HOUR', observed_ts)"""


def build_weather(spark: Any, prefix: str, landing_volume: str) -> None:
    """Parse landed files not yet ingested, merge new observations, rebuild the hourly table."""

    from pyspark.sql import functions as F

    silver = f"workspace.{prefix}_silver"
    target = f"{silver}.{OBSERVATIONS}"
    root = volume_path(landing_volume, "weather")
    listing = (
        spark.read.format("binaryFile")
        .option("recursiveFileLookup", "true")
        .load(root)
        .select("path")
    )
    paths = [r["path"] for r in listing.collect()]
    done: set[str] = set()
    if spark.catalog.tableExists(target):
        done = {
            r["source_file"] for r in spark.table(target).select("source_file").distinct().collect()
        }
    new = [p for p in paths if p.split("/")[-1] not in done]
    print(f"weather files landed: {len(paths)}, new: {len(new)}")
    rows: list[tuple[Any, ...]] = []
    if new:
        content = spark.read.format("binaryFile").load(new).select("path", "content").collect()
        for r in content:
            name = r["path"].split("/")[-1]
            for o in parse_file(bytes(r["content"]).decode("utf-8")):
                rows.append(
                    (
                        o["station"],
                        o["observed_ts"],
                        o["temp_c"],
                        o["wind_kmh"],
                        o["gust_kmh"],
                        o["visibility_m"],
                        o["humidity_pct"],
                        o["pressure_pa"],
                        o["text"],
                        o["precip_level"],
                        o["fog"],
                        o["codes"],
                        name,
                        None,
                    )
                )
        frame = spark.createDataFrame(rows, OBS_DDL).withColumn(
            "ingested_at", F.current_timestamp()
        )
        if not spark.catalog.tableExists(target):
            frame.write.format("delta").mode("append").saveAsTable(target)
        else:
            frame.createOrReplaceTempView("incoming_weather")
            spark.sql(
                f"MERGE INTO {target} t USING (SELECT * FROM incoming_weather) s ON t.station <=> s.station AND t.observed_ts <=> s.observed_ts WHEN NOT MATCHED THEN INSERT *"
            )
    n = spark.table(target).count()
    print(f"weather observations: {n} rows ({len(rows)} parsed this run)")
    spark.sql(f"CREATE OR REPLACE TABLE {silver}.{HOURLY} AS {hourly_sql(prefix)}")
    print(f"weather hourly: {spark.table(f'{silver}.{HOURLY}').count()} rows")
