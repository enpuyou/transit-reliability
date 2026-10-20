# ruff: noqa: E501  (SQL is kept on readable single lines so it can be pasted into the editor)
"""Checks that run once a day after the silver job, on the newest COMPLETE service date.

The independent audit checks (`audit/catalog.py`) plus drift checks: does the day look like the days
before it? Drift thresholds are wide: they catch a shifted time zone, a collapsed join or a broken
unit, not a bad-weather day.
"""

import datetime as dt

from transit_reliability.audit.catalog import all_checks
from transit_reliability.audit.core import CheckSpec

COMPLETE_AFTER_DAYS = 2  # after-midnight trips make a service date complete two days later
MAX_MEDIAN_DELAY_SHIFT_S = (
    90.0  # healthy days differ by under 10 s (111, 120, 114, 118 on 10-14..17)
)
MAX_COVERAGE_SHIFT = 0.1  # healthy days are 85 to 87 percent
MAX_ZERO_WIDTH_SHARE = (
    0.01  # a few exact times are fine (a fix right at the stop); 9.5 percent is not
)
MIN_RIDER_SCORED_SHARE = 0.8  # about 91 percent of non-last timepoints are scored (2026-10-16)
MIN_OBSERVED_TRIP_SHARE = 0.9  # about 96 percent of scheduled trips are observed


def complete_day(today: dt.date) -> dt.date:
    return today - dt.timedelta(days=COMPLETE_AFTER_DAYS)


def drift_checks(prefix: str, day: str) -> list[CheckSpec]:
    sv = f"workspace.{prefix}_silver.stop_visits"
    tp = f"workspace.{prefix}_gold.trip_performance"
    window = f"service_date BETWEEN date_sub(DATE'{day}', 3) AND DATE'{day}'"
    return [
        CheckSpec(
            "median_timepoint_delay_shift_vs_previous_3_days_seconds",
            f"SELECT abs(percentile(arr_delay_s, 0.5) FILTER (WHERE service_date = DATE'{day}') - percentile(arr_delay_s, 0.5) FILTER (WHERE service_date < DATE'{day}')) FROM {sv} WHERE {window} AND timepoint = 1 AND arr_delay_s IS NOT NULL",
            "a sudden shift of the typical delay is a time-zone, join or unit error until shown otherwise",
            maximum=MAX_MEDIAN_DELAY_SHIFT_S,
        ),
        CheckSpec(
            "arrival_coverage_shift_vs_previous_3_days",
            f"SELECT abs(avg(CASE WHEN arr_est IS NOT NULL THEN 1.0 ELSE 0.0 END) FILTER (WHERE service_date = DATE'{day}') - avg(CASE WHEN arr_est IS NOT NULL THEN 1.0 ELSE 0.0 END) FILTER (WHERE service_date < DATE'{day}')) FROM {sv} WHERE {window}",
            "the share of scheduled stop visits with a measured arrival should be steady from day to day",
            maximum=MAX_COVERAGE_SHIFT,
        ),
        CheckSpec(
            "zero_width_arrival_interval_share",
            f"SELECT avg(CASE WHEN arr_lo = arr_hi THEN 1.0 ELSE 0.0 END) FROM {sv} WHERE service_date = DATE'{day}' AND arr_est IS NOT NULL",
            "an arrival 'known' to the second is suspicious: before the fix of decision 0019 about 9 percent of arrivals were collapsed to one instant by the feed's next-stop pointer or a dwell report arriving before the GPS bound",
            maximum=MAX_ZERO_WIDTH_SHARE,
        ),
        CheckSpec(
            "rider_rule_scored_share_of_non_last_timepoints",
            f"SELECT (SELECT sum(n_rider_early + n_rider_on_time + n_rider_late + n_rider_uncertain) FROM {tp} WHERE service_date = DATE'{day}') / (SELECT count(*) FROM (SELECT timepoint, row_number() OVER (PARTITION BY trip_id ORDER BY stop_sequence) rn, count(*) OVER (PARTITION BY trip_id) cnt FROM {sv} WHERE service_date = DATE'{day}') WHERE timepoint = 1 AND rn < cnt)",
            "the headline rule scores a timepoint when its arrival or departure was estimated; the share of all non-last timepoints it can score should stay near 90 percent",
            minimum=MIN_RIDER_SCORED_SHARE,
        ),
        CheckSpec(
            "observed_trip_share",
            f"SELECT avg(CASE WHEN trip_status = 'observed' THEN 1.0 ELSE 0.0 END) FROM {tp} WHERE service_date = DATE'{day}'",
            "most scheduled trips should have been seen on the road (canceled and never-seen trips are a small remainder)",
            minimum=MIN_OBSERVED_TRIP_SHARE,
        ),
    ]


def daily_checks(prefix: str, day: dt.date) -> list[CheckSpec]:
    return [*all_checks(prefix, day.isoformat()), *drift_checks(prefix, day.isoformat())]
