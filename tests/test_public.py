import datetime as dt
import decimal
import json
import re

from transit_reliability.public.build import ATTRIBUTION, build_payload, plain
from transit_reliability.public.catalog import GLOSSARY, LAYERS, TABLES
from transit_reliability.public.queries import heavy_queries, live_queries, sample_queries


def test_every_documented_table_has_one_sample_query_and_the_reverse() -> None:
    documented = {f"{t.layer}.{t.name}" for t in TABLES}
    assert documented == set(sample_queries("transit"))
    assert len(documented) == len(TABLES)  # no duplicates
    assert {t.layer for t in TABLES} <= {layer.id for layer in LAYERS}


def test_samples_never_expose_a_raw_vehicle_id_or_exact_coordinates() -> None:
    for name, (_, sql) in sample_queries("transit").items():
        masked = "concat('veh-', substr(sha2(vehicle_id, 256), 1, 6)) AS vehicle_id"
        leftover = sql.replace(masked, "").replace("vehicle_id IS NOT NULL", "")
        assert "vehicle_id" not in leftover, name  # only the hashed form may appear
        for column in ("latitude", "longitude"):
            leftover = leftover.replace(f"round({column}, 2) AS {column}", "")
            assert column not in leftover, (name, column)  # only the rounded form may appear
        assert "struct(*)" not in sql or name in {
            "silver.scheduled_trips",
            "gold.prediction_accuracy",
        }


def test_aggregate_queries_never_select_vehicle_ids_or_coordinates() -> None:
    for group in (live_queries("transit"), heavy_queries("transit")):
        for name, sql in group.items():
            assert "vehicle_id" not in sql and "latitude" not in sql, name
            assert "longitude" not in sql, name


def test_queries_use_the_given_prefix_and_never_write() -> None:
    for group in (live_queries("dev_transit"), heavy_queries("dev_transit")):
        for sql in group.values():
            assert not re.search(r"\b(INSERT|DELETE|DROP|UPDATE|MERGE)\b", sql, re.IGNORECASE)
            assert "workspace.transit_" not in sql
    assert "workspace.dev_transit_bronze." in live_queries("dev_transit")["hourly_snapshots"]


def test_plain_makes_spark_values_json_safe() -> None:
    value = {
        "d": dt.date(2026, 10, 16),
        "t": dt.datetime(2026, 10, 16, 1, 2, 3),
        "x": decimal.Decimal("1.5"),
        "l": [dt.date(2026, 1, 1)],
    }
    assert json.loads(json.dumps(plain(value))) == {
        "d": "2026-10-16",
        "t": "2026-10-16T01:02:03",
        "x": 1.5,
        "l": ["2026-01-01"],
    }


def test_the_site_file_carries_the_attribution_the_explanations_and_any_errors() -> None:
    payload = build_payload(
        {"job_runs": [{"a": 1}]},
        {"billing_daily": "boom"},
        "transit",
        dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
    )
    assert payload["attribution"] == ATTRIBUTION and "King County" in ATTRIBUTION
    assert [layer["id"] for layer in payload["layers"]][:2] == ["collector", "landing"]
    assert payload["errors"] == {"billing_daily": "boom"} and payload["glossary"] == GLOSSARY
    assert payload["tables"]["job_runs"] == [{"a": 1}]


def test_billing_is_grouped_into_a_few_readable_categories() -> None:
    sql = heavy_queries("transit")["billing_daily"]
    labels = set(re.findall(r"THEN '([^']+)'", sql)) | set(re.findall(r"ELSE '([^']+)'", sql))
    assert len(labels) == 6  # three prod jobs, one-off runs, the SQL warehouse, everything else


def test_day_buckets_are_seattle_days_and_the_pointer_lead_is_published() -> None:
    heavy = heavy_queries("transit")
    for name in ("daily_metrics", "check_trend", "billing_daily"):
        assert "America/Los_Angeles" in heavy[name], name
    assert "pointer_lead_feet" in heavy["distributions"]
    assert "pointer_lead_ft IS NOT NULL" in heavy["distributions"]
