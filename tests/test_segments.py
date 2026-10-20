import datetime as dt
import re

from transit_reliability.gtfs_static.gold_segments import (
    DETOUR_WORDS,
    band_sql,
    day_type_sql,
    route_mention_pattern,
    segment_runs_sql,
    segment_runtimes_sql,
)


def names_route(headline: str, short_name: str) -> bool:
    return bool(re.search(route_mention_pattern(short_name), headline))


def test_an_alert_naming_a_route_matches_in_each_phrasing_the_agency_uses() -> None:
    assert names_route(
        "Route 77 to University District will be rerouted off portions of NE 130th St", "77"
    )
    assert names_route("Routes 75 and 77 will be rerouted in the U-district area", "77")
    assert names_route("Routes 77 and 75 will be rerouted in the UW Campus area", "77")
    assert names_route("Routes 3, 4 and 77 are rerouted", "77")
    assert names_route("route 77 detour", "77")  # case does not matter


def test_the_pattern_does_not_match_other_routes_or_street_numbers() -> None:
    assert not names_route("Route 773 to Seacrest Marina is rerouted off SW Avalon Way", "77")
    assert not names_route("Route 7 is rerouted off 1st Ave", "1")
    assert not names_route("Route 7 is rerouted off 1st Ave", "77")
    assert not names_route("Route 70 rerouted near 77th Ave", "77")
    assert not names_route("Route 77A will be rerouted", "77")
    assert not names_route("Service on 77 is unchanged", "77")  # no 'Route' word


def test_only_reroute_or_detour_alerts_count() -> None:
    assert re.search(DETOUR_WORDS, "Route 77 will be rerouted off 30th Ave NE")
    assert re.search(DETOUR_WORDS, "Detour on Route 5")
    assert not re.search(
        DETOUR_WORDS, "Route 77 trip to University District is not operating today"
    )


def test_time_bands_and_day_types_cover_every_hour_and_weekday() -> None:
    sql = band_sql("h")
    for token in ("am_peak", "midday", "pm_peak", "evening", "night"):
        assert token in sql
    assert "dayofweek" in day_type_sql("d") and "'weekday'" in day_type_sql("d")


def test_segment_sql_is_scoped_to_the_day_and_prefix_and_only_reads() -> None:
    sql = segment_runs_sql("transit", dt.date(2026, 10, 16))
    assert "workspace.transit_silver.stop_visits" in sql and "DATE'2026-10-16'" in sql
    assert "timepoint = 1" in sql and "workspace.transit_bronze.alerts" in sql
    assert not re.search(r"\b(INSERT|DELETE|DROP|UPDATE|MERGE)\b", sql, re.IGNORECASE)
    summary = segment_runtimes_sql("transit", dt.date(2026, 10, 16))
    assert "DATE'2026-09-19'" in summary and "DATE'2026-10-16'" in summary  # 28 days, inclusive
    assert "HAVING count(*) >= 10" in summary
