# ruff: noqa: E501  (SQL is kept on readable single lines so it can be pasted into the editor)
"""The audit checks. Each returns one number; the rule says what a healthy pipeline gives.

Written from the table contracts and GTFS meaning, not from the build code, so a mistake in the
build cannot also hide here. `prefix` is `transit` (prod) or `dev_transit`. `day` is a service date
(ISO text) that is complete: pass a date at least two days old, or partial-day results mislead.
"""

from dataclasses import replace

from transit_reliability.audit.core import CheckSpec

MAX_GAP_S = 180.0  # the width an "interpolated" arrival interval may not exceed (arrivals/infer.py)
MPH_PER_FT_S = 0.681818


def _t(prefix: str, layer: str, table: str) -> str:
    return f"workspace.{prefix}_{layer}.{table}"


def bronze_to_silver(prefix: str) -> list[CheckSpec]:
    """Nothing silently dropped or duplicated between bronze and silver (same cutoff both sides)."""

    bvp, svp, q = (
        _t(prefix, "bronze", "vehicle_positions"),
        _t(prefix, "silver", "vehicle_positions"),
        _t(prefix, "silver", "quarantine_vehicle_positions"),
    )
    cutoff = f"(SELECT greatest(max(bronze_ingested_at), (SELECT max(bronze_ingested_at) FROM {q})) FROM {svp})"
    bad = (
        "(vehicle_ts IS NULL OR vehicle_ts = 0 OR latitude IS NULL OR longitude IS NULL OR latitude = 0 OR longitude = 0"
        " OR NOT (latitude BETWEEN 46.9 AND 48.1 AND longitude BETWEEN -122.8 AND -121.0)"
        " OR vehicle_ts > feed_header_ts + 60 OR vehicle_ts < feed_header_ts - 3600"
        " OR vehicle_id IS NULL OR vehicle_id = '' OR trip_id IS NULL OR trip_id = '')"
    )
    base = f"FROM {bvp} WHERE source_file NOT LIKE '%smoke%' AND ingested_at <= {cutoff}"
    tu_b, tu_s = _t(prefix, "bronze", "trip_updates"), _t(prefix, "silver", "trip_update_snapshots")
    return [
        CheckSpec(
            "silver_vp_equals_distinct_good_bronze_keys",
            f"SELECT abs((SELECT count(*) FROM {svp}) - (SELECT count(DISTINCT vehicle_id, vehicle_ts) {base} AND NOT {bad}))",
            "silver fixes = distinct (vehicle_id, vehicle_ts) of bronze rows that pass the rules, up to the silver watermark",
            maximum=0,
        ),
        CheckSpec(
            "quarantine_equals_bad_bronze_rows",
            f"SELECT abs((SELECT count(*) FROM {q}) - (SELECT count(*) {base} AND {bad}))",
            "every bronze row that breaks a rule is in quarantine exactly once",
            maximum=0,
        ),
        CheckSpec(
            "silver_vp_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT vehicle_id, vehicle_ts) FROM {svp}",
            "the documented key (vehicle_id, vehicle_ts) is unique",
            maximum=0,
        ),
        CheckSpec(
            "silver_trip_update_snapshots_equal_bronze_distinct_snapshots",
            f"SELECT abs((SELECT sum(snapshots) FROM {tu_s}) - (SELECT count(DISTINCT trip_id, start_date, stop_sequence, feed_header_ts) FROM {tu_b} WHERE source_file NOT LIKE '%smoke%' AND trip_id IS NOT NULL AND start_date RLIKE '^[0-9]{{8}}$' AND feed_header_ts <= (SELECT max(unix_timestamp(last_seen)) FROM {tu_s})))",
            "run lengths add up to the number of distinct bronze snapshots (no prediction lost or counted twice)",
            maximum=0,
        ),
        CheckSpec(
            "silver_trip_update_run_key_duplicates",
            f"SELECT count(*) - count(DISTINCT trip_id, service_date, stop_sequence, first_seen) FROM {tu_s}",
            "a run is identified by (trip_id, service_date, stop_sequence, first_seen)",
            maximum=0,
        ),
        CheckSpec(
            "silver_vp_null_service_date",
            f"SELECT count(*) FROM {svp} WHERE service_date IS NULL",
            "a fix without a service date can never reach stop_visits",
            maximum=0,
        ),
    ]


def schedule(prefix: str, day: str) -> list[CheckSpec]:
    """GTFS time semantics: times past 24:00 and the Pacific wall clock."""

    sst, gst = _t(prefix, "silver", "scheduled_stop_times"), _t(prefix, "silver", "gtfs_stop_times")
    wall = "concat_ws(':', lpad(CAST(g.arrival_seconds DIV 3600 % 24 AS STRING), 2, '0'), lpad(CAST(g.arrival_seconds % 3600 DIV 60 AS STRING), 2, '0'), lpad(CAST(g.arrival_seconds % 60 AS STRING), 2, '0'))"
    return [
        CheckSpec(
            "scheduled_time_wall_clock_mismatch",
            f"SELECT count(*) FROM {sst} s JOIN {gst} g ON g.trip_id = s.trip_id AND g.stop_sequence = s.stop_sequence AND g.feed_version = s.feed_version WHERE s.service_date = DATE'{day}' AND date_format(from_utc_timestamp(s.scheduled_arrival_ts, 'America/Los_Angeles'), 'HH:mm:ss') <> {wall}",
            "scheduled UTC timestamp, read back in Pacific time, equals the GTFS time modulo 24 h (covers after-midnight and DST)",
            maximum=0,
        ),
        CheckSpec(
            "scheduled_stop_times_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT trip_id, stop_sequence) FROM {sst} WHERE service_date = DATE'{day}'",
            "one row per (service_date, trip_id, stop_sequence)",
            maximum=0,
        ),
    ]


def stop_visits(prefix: str, day: str) -> list[CheckSpec]:
    """Are the inferred arrival and departure times physically and logically possible?"""

    sv = _t(prefix, "silver", "stop_visits")
    gst = _t(prefix, "silver", "gtfs_stop_times")
    d = f"service_date = DATE'{day}'"
    pairs = f"""(SELECT v.trip_id, v.stop_sequence, v.arr_est, v.arr_method,
        lag(v.dep_est) OVER (PARTITION BY v.trip_id ORDER BY v.stop_sequence) pdep,
        lag(g.shape_dist_traveled) OVER (PARTITION BY v.trip_id ORDER BY v.stop_sequence) pdist, g.shape_dist_traveled dist
        FROM {sv} v JOIN {gst} g ON g.trip_id = v.trip_id AND g.stop_sequence = v.stop_sequence
        WHERE v.{d} AND v.arr_est IS NOT NULL)"""
    return [
        CheckSpec(
            "stop_visits_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT trip_id, stop_sequence) FROM {sv} WHERE {d}",
            "grain is (service_date, trip_id, stop_sequence)",
            maximum=0,
        ),
        CheckSpec(
            "arrival_estimate_outside_its_interval",
            f"SELECT count(*) FROM {sv} WHERE {d} AND (arr_est < arr_lo OR arr_est > arr_hi)",
            "a point estimate lies inside [lo, hi]",
            maximum=0,
        ),
        CheckSpec(
            "departure_before_arrival",
            f"SELECT count(*) FROM {sv} WHERE {d} AND dep_est < arr_est",
            "a bus cannot leave before it arrives",
            maximum=0,
        ),
        CheckSpec(
            "interpolated_interval_wider_than_its_definition_share",
            f"SELECT avg(CASE WHEN unix_timestamp(arr_hi) - unix_timestamp(arr_lo) > {MAX_GAP_S} THEN 1.0 ELSE 0.0 END) FROM {sv} WHERE {d} AND arr_method = 'interpolated'",
            "'interpolated' means two fixes at most 180 s apart bracket the arrival; wider intervals are mislabeled (stale next-stop pointer on a trip's first fix, then order enforcement)",
            maximum=0.001,
        ),
        CheckSpec(
            "zero_travel_time_between_distant_stops_share",
            f"SELECT avg(CASE WHEN unix_timestamp(arr_est) = unix_timestamp(pdep) AND dist - pdist > 300 THEN 1.0 ELSE 0.0 END) FROM {pairs} WHERE pdep IS NOT NULL",
            "arriving at the next stop in 0 s although it is more than 300 ft further means the arrival was copied from the previous departure",
            maximum=0.005,
        ),
        CheckSpec(
            "implied_speed_over_68mph_share",
            f"SELECT avg(CASE WHEN unix_timestamp(arr_est) > unix_timestamp(pdep) AND (dist - pdist) / (unix_timestamp(arr_est) - unix_timestamp(pdep)) > 100 THEN 1.0 ELSE 0.0 END) FROM {pairs} WHERE pdep IS NOT NULL",
            "feet per second between consecutive measured stops, shape_dist_traveled being in feet (68 mph = 100 ft/s)",
            maximum=0.005,
        ),
        CheckSpec(
            "median_speed_between_stops_mph_plausible",
            f"SELECT percentile(CASE WHEN unix_timestamp(arr_est) > unix_timestamp(pdep) THEN (dist - pdist) / (unix_timestamp(arr_est) - unix_timestamp(pdep)) * {MPH_PER_FT_S} END, 0.5) FROM {pairs} WHERE pdep IS NOT NULL",
            "an urban bus moves between stops at roughly 8 to 30 mph; far outside means a unit or ordering error",
            minimum=8,
            maximum=30,
        ),
        CheckSpec(
            "arrival_delay_median_abs_seconds_plausible",
            f"SELECT percentile(abs(arr_delay_s), 0.5) FROM {sv} WHERE {d} AND arr_delay_s IS NOT NULL",
            "median |delay| of a day's arrivals should be minutes, not hours (a service-day or time-zone error shifts it by 3600 s)",
            maximum=600,
        ),
        CheckSpec(
            "arrival_delay_beyond_2h_share",
            f"SELECT avg(CASE WHEN abs(arr_delay_s) > 7200 THEN 1.0 ELSE 0.0 END) FROM {sv} WHERE {d} AND arr_delay_s IS NOT NULL",
            "delays over two hours are almost certainly a matching error",
            maximum=0.001,
        ),
    ]


def coverage(prefix: str, day: str) -> list[CheckSpec]:
    """How much of the headline denominator can ever be measured?"""

    sv = _t(prefix, "silver", "stop_visits")
    d = f"service_date = DATE'{day}'"
    ranked = f"(SELECT *, row_number() OVER (PARTITION BY trip_id ORDER BY stop_sequence) rs, row_number() OVER (PARTITION BY trip_id ORDER BY stop_sequence DESC) re FROM {sv} WHERE {d} AND timepoint = 1)"
    return [
        CheckSpec(
            "timepoints_with_an_interval_share",
            f"SELECT avg(CASE WHEN arr_lo IS NOT NULL AND arr_hi IS NOT NULL THEN 1.0 ELSE 0.0 END) FROM {sv} WHERE {d} AND timepoint = 1",
            "on-time shares are computed over measured timepoints only; the unmeasured part is not in any denominator",
            minimum=0.85,
        ),
        CheckSpec(
            "first_timepoint_of_trip_measured_share",
            f"SELECT avg(CASE WHEN arr_lo IS NOT NULL AND arr_hi IS NOT NULL THEN 1.0 ELSE 0.0 END) FROM {ranked} WHERE rs = 1",
            "the first stop of every trip is a timepoint; if it is never measured, trip-start punctuality is missing from on-time shares",
            minimum=0.5,
        ),
        CheckSpec(
            "last_timepoint_of_trip_measured_share",
            f"SELECT avg(CASE WHEN arr_lo IS NOT NULL AND arr_hi IS NOT NULL THEN 1.0 ELSE 0.0 END) FROM {ranked} WHERE re = 1",
            "the last stop is where accumulated lateness is largest; if it is never measured, on-time shares skew optimistic",
            minimum=0.5,
        ),
    ]


def gold(prefix: str, day: str) -> list[CheckSpec]:
    """Gold recomputes from silver and conserves counts down the roll-ups."""

    sv = _t(prefix, "silver", "stop_visits")
    st = _t(prefix, "silver", "scheduled_trips")
    tp, rp = _t(prefix, "gold", "trip_performance"), _t(prefix, "gold", "route_performance")
    pe, pa = _t(prefix, "gold", "prediction_errors"), _t(prefix, "gold", "prediction_accuracy")
    d = f"service_date = DATE'{day}'"
    dlo = "(unix_timestamp(arr_lo) - unix_timestamp(sched_arr))"
    dhi = "(unix_timestamp(arr_hi) - unix_timestamp(sched_arr))"
    base = f"{d} AND timepoint = 1 AND arr_lo IS NOT NULL AND arr_hi IS NOT NULL"
    return [
        CheckSpec(
            "gold_common_on_time_equals_recount_from_stop_visits",
            f"SELECT abs((SELECT sum(n_common_on_time) FROM {tp} WHERE {d}) - (SELECT count(*) FROM {sv} WHERE {base} AND {dlo} >= -60 AND {dhi} <= 300))",
            "definitely-on-time (whole interval within 1 min early to 5 min late) recounted straight from stop_visits",
            maximum=0,
        ),
        CheckSpec(
            "gold_common_lens_counts_equal_measured_timepoints",
            f"SELECT abs((SELECT sum(n_common_early + n_common_on_time + n_common_late + n_common_uncertain) FROM {tp} WHERE {d}) - (SELECT count(*) FROM {sv} WHERE {base}))",
            "early + on time + late + uncertain partition the measured timepoints exactly",
            maximum=0,
        ),
        CheckSpec(
            "gold_rider_counts_equal_recount_from_stop_visits",
            (
                f"WITH v AS (SELECT row_number() OVER (PARTITION BY trip_id ORDER BY stop_sequence) rn, count(*) OVER (PARTITION BY trip_id) cnt, timepoint, "
                f"unix_timestamp(arr_lo) - unix_timestamp(sched_arr) alo, unix_timestamp(arr_hi) - unix_timestamp(sched_arr) ahi, "
                f"unix_timestamp(dep_lo) - unix_timestamp(sched_dep) plo, unix_timestamp(dep_hi) - unix_timestamp(sched_dep) phi FROM {sv} WHERE {d}), "
                f"s AS (SELECT *, coalesce(phi < -60, false) AS is_early, coalesce((rn > 1 AND alo > 300) OR (rn = 1 AND plo > 300), false) AS is_late FROM v WHERE timepoint = 1 AND rn < cnt AND (alo IS NOT NULL OR plo IS NOT NULL)), "
                f"c AS (SELECT count(*) total, count_if(is_early) early, count_if(NOT is_early AND is_late) late, "
                f"count_if(NOT is_early AND NOT is_late AND coalesce(greatest(alo, plo) >= -60, false) AND coalesce(least(ahi, phi) <= 300, false)) on_time FROM s) "
                f"SELECT abs(c.early - g.e) + abs(c.late - g.l) + abs(c.on_time - g.o) + abs(c.total - g.t) + abs((c.total - c.early - c.late - c.on_time) - g.u) "
                f"FROM c, (SELECT sum(n_rider_early) e, sum(n_rider_late) l, sum(n_rider_on_time) o, sum(n_rider_uncertain) u, "
                f"sum(n_rider_early + n_rider_on_time + n_rider_late + n_rider_uncertain) t FROM {tp} WHERE {d}) g"
            ),
            "the headline rider rule (early on departure, late on arrival, last stop out) recounted straight from stop_visits with a separately written formulation",
            maximum=0,
        ),
        CheckSpec(
            "route_rollup_rider_counts_conserve",
            f"SELECT abs((SELECT sum(n_rider_early + n_rider_on_time + n_rider_late + n_rider_uncertain) FROM {rp} WHERE {d}) - (SELECT sum(n_rider_early + n_rider_on_time + n_rider_late + n_rider_uncertain) FROM {tp} WHERE {d}))",
            "route_performance adds the rider counts of trip_performance without losing or inventing any",
            maximum=0,
        ),
        CheckSpec(
            "trip_performance_rows_equal_scheduled_trips",
            f"SELECT abs((SELECT count(*) FROM {tp} WHERE {d}) - (SELECT count(*) FROM {st} WHERE {d}))",
            "one gold row per scheduled trip: none missing, none duplicated",
            maximum=0,
        ),
        CheckSpec(
            "trip_performance_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT trip_id) FROM {tp} WHERE {d}",
            "grain is (service_date, trip_id)",
            maximum=0,
        ),
        CheckSpec(
            "route_rollup_on_time_conserves",
            f"SELECT abs((SELECT sum(n_common_on_time) FROM {rp} WHERE {d}) - (SELECT sum(n_common_on_time) FROM {tp} WHERE {d}))",
            "route_performance sums trip_performance (counts are added, never averaged)",
            maximum=0,
        ),
        CheckSpec(
            "route_rollup_trip_statuses_add_up",
            f"SELECT count(*) FROM {rp} WHERE {d} AND n_trips <> n_observed + n_canceled + n_not_observed",
            "every trip is observed, canceled, or not observed",
            maximum=0,
        ),
        CheckSpec(
            "prediction_accuracy_conserves_prediction_errors",
            f"SELECT abs((SELECT sum(n) FROM {pa} WHERE {d}) - (SELECT count(*) FROM {pe} WHERE {d} AND width_s <= 60))",
            "accuracy rows count exactly the prediction errors whose actual arrival is known within 60 s",
            maximum=0,
        ),
        CheckSpec(
            "prediction_error_beyond_1h_share",
            f"SELECT avg(CASE WHEN abs(kcm_error_s) > 3600 THEN 1.0 ELSE 0.0 END) FROM {pe} WHERE {d}",
            "errors of an hour or more (some are exactly one day: a prediction for the next day's run of the same trip_id) distort any mean",
            maximum=0.0001,
        ),
    ]


# Rules that are broken today for reasons we understand and have written down (decision 0016).
# They are recorded and shown as warnings, not failures, so a real new failure stays visible.
KNOWN_LIMITS = frozenset(
    {
        "timepoints_with_an_interval_share",  # trip starts and ends are mostly unmeasured
        "first_timepoint_of_trip_measured_share",
        "last_timepoint_of_trip_measured_share",
        "prediction_error_beyond_1h_share",  # next-day runs of the same trip_id
    }
)


def segments(prefix: str, day: str) -> list[CheckSpec]:
    """Segment running times recompute from stop visits, are positive, and are plausible."""

    sv = _t(prefix, "silver", "stop_visits")
    sr, rt = _t(prefix, "gold", "segment_runs"), _t(prefix, "gold", "segment_runtimes")
    d = f"service_date = DATE'{day}'"
    return [
        CheckSpec(
            "segment_runs_equal_independent_pair_count",
            (
                f"WITH tp AS (SELECT trip_id, stop_sequence, sched_arr, sched_dep, arr_est, dep_est FROM {sv} WHERE {d} AND timepoint = 1), "
                f"nxt AS (SELECT a.trip_id, a.stop_sequence s1, min(b.stop_sequence) s2 FROM tp a JOIN tp b ON a.trip_id = b.trip_id AND b.stop_sequence > a.stop_sequence GROUP BY 1, 2), "
                f"pairs AS (SELECT count(*) n FROM nxt JOIN tp a ON a.trip_id = nxt.trip_id AND a.stop_sequence = nxt.s1 JOIN tp b ON b.trip_id = nxt.trip_id AND b.stop_sequence = nxt.s2 "
                f"WHERE a.dep_est IS NOT NULL AND b.arr_est IS NOT NULL AND unix_timestamp(b.sched_arr) > unix_timestamp(a.sched_dep)) "
                f"SELECT abs((SELECT n FROM pairs) - (SELECT count(*) FROM {sr} WHERE {d}))"
            ),
            "one segment run per pair of consecutive timepoints that has a departure, an arrival and a positive scheduled runtime, recounted with a self-join instead of lead()",
            maximum=0,
        ),
        CheckSpec(
            "segment_runs_scheduled_runtime_not_positive",
            f"SELECT count(*) FROM {sr} WHERE {d} AND sched_s <= 0",
            "a scheduled running time is the gap between a departure and the next arrival, never zero or negative",
            maximum=0,
        ),
        CheckSpec(
            "segment_runs_negative_observed_runtime_share",
            f"SELECT avg(CASE WHEN obs_s < 0 THEN 1.0 ELSE 0.0 END) FROM {sr} WHERE {d}",
            "a bus cannot arrive at the next timepoint before it left this one; a few can appear when two estimates overlap",
            maximum=0.001,
        ),
        CheckSpec(
            "segment_runs_median_observed_over_scheduled_ratio",
            f"SELECT percentile(obs_s / sched_s, 0.5) FROM {sr} WHERE {d} AND sched_s >= 120",
            "buses take roughly the scheduled time between timepoints (0.95 on 2026-10-14 to 10-16); far outside means a unit or matching error",
            minimum=0.6,
            maximum=1.4,
        ),
        CheckSpec(
            "segment_runtimes_summary_is_current",
            f"SELECT CASE WHEN count(*) < 500 THEN 999 ELSE abs(datediff(DATE'{day}', max(last_day))) END FROM {rt}",
            "the trailing-window summary has at least 500 segment rows and ends at the newest complete day",
            maximum=1,
        ),
    ]


def headways(prefix: str, day: str) -> list[CheckSpec]:
    """Headway pairs recompute from stop visits, every pair gets a class, and the shares are plausible."""

    sv = _t(prefix, "silver", "stop_visits")
    hp, hs = _t(prefix, "gold", "headway_pairs"), _t(prefix, "gold", "headway_summary")
    d = f"service_date = DATE'{day}'"
    return [
        CheckSpec(
            "headway_pairs_equal_independent_recount",
            (
                f"WITH tp AS (SELECT route_id, direction_id, stop_id, trip_id, sched_dep, row_number() OVER (PARTITION BY route_id, direction_id, stop_id ORDER BY sched_dep, trip_id) rn "
                f"FROM {sv} WHERE {d} AND timepoint = 1 AND dep_est IS NOT NULL), "
                f"n AS (SELECT count(*) c FROM tp a JOIN tp b ON a.route_id = b.route_id AND a.direction_id = b.direction_id AND a.stop_id = b.stop_id AND b.rn = a.rn + 1 "
                f"WHERE unix_timestamp(b.sched_dep) - unix_timestamp(a.sched_dep) BETWEEN 1 AND 3600) "
                f"SELECT abs((SELECT c FROM n) - (SELECT count(*) FROM {hp} WHERE {d}))"
            ),
            "one headway pair per consecutive pair of departures scheduled up to an hour apart at the same route, direction and timepoint, recounted with a row-number join instead of lag()",
            maximum=0,
        ),
        CheckSpec(
            "headway_pairs_without_a_class_share",
            f"SELECT avg(CASE WHEN outcome IS NULL THEN 1.0 ELSE 0.0 END) FROM {hp} WHERE {d}",
            "every pair has bunched, gapped, regular or uncertain; a missing class means a missing bound",
            maximum=0.001,
        ),
        CheckSpec(
            "headway_median_actual_over_scheduled_ratio_frequent",
            f"SELECT percentile(act_h / sched_h, 0.5) FROM {hp} WHERE {d} AND frequent",
            "buses on frequent routes are spaced about as scheduled on a typical day (1.00 measured 2026-10-16)",
            minimum=0.85,
            maximum=1.15,
        ),
        CheckSpec(
            "headway_bunched_share_of_frequent_pairs",
            f"SELECT avg(CASE WHEN outcome = 'bunched' THEN 1.0 ELSE 0.0 END) FROM {hp} WHERE {d} AND frequent",
            "pairs certainly under a quarter of the scheduled gap (3.3 percent by point estimate on 2026-10-16); far more means a pairing or ordering error",
            maximum=0.15,
        ),
        CheckSpec(
            "headway_summary_is_current",
            f"SELECT CASE WHEN count(*) < 100 THEN 999 ELSE abs(datediff(DATE'{day}', max(built_through))) END FROM {hs}",
            "the trailing-window headway summary has at least 100 rows and was built through the newest complete day",
            maximum=1,
        ),
    ]


def delivery(prefix: str, day: str) -> list[CheckSpec]:
    """trip_delivery has one row per scheduled trip of the day, each with an outcome."""

    td, st = _t(prefix, "gold", "trip_delivery"), _t(prefix, "silver", "scheduled_trips")
    return [
        CheckSpec(
            "trip_delivery_rows_match_schedule",
            f"SELECT abs((SELECT count(*) FROM {td} WHERE service_date = DATE'{day}') - (SELECT count(*) FROM {st} WHERE service_date = DATE'{day}'))",
            "one row for every scheduled trip of the day, no more, no fewer",
            maximum=0,
        ),
        CheckSpec(
            "trip_delivery_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT trip_id) FROM {td} WHERE service_date = DATE'{day}'",
            "one row per trip and service date",
            maximum=0,
        ),
        CheckSpec(
            "trip_delivery_without_outcome",
            f"SELECT count(*) FROM {td} WHERE service_date = DATE'{day}' AND outcome IS NULL",
            "every trip gets one of the six outcomes",
            maximum=0,
        ),
    ]


def ml_examples(prefix: str, day: str) -> list[CheckSpec]:
    """The training table never contains information from after its moment (the "as of t" rule)."""

    t = _t(prefix, "ml", "arrival_examples")
    where = f"service_date = DATE'{day}'"
    leaks = {
        "ml_examples_leak_state_published_after_moment": "state_visible_at > as_of",
        "ml_examples_leak_agency_snapshot_after_moment": "kcm_first_seen > as_of",
        "ml_examples_leak_history_not_from_before": "hist_last_day >= service_date",
        "ml_examples_leak_target_already_reached": "actual_ts <= as_of",
        "ml_examples_leak_now_stop_not_before_target": "last_seq >= stop_sequence",
    }
    out = [
        CheckSpec(
            name,
            f"SELECT count(*) FROM {t} WHERE {where} AND {cond}",
            "a feature built from information not yet published at the example's moment would let a model "
            "peek at the future; this must be zero",
            maximum=0,
        )
        for name, cond in leaks.items()
    ]
    out.append(
        CheckSpec(
            "ml_examples_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT trip_id, stop_sequence, as_of) FROM {t} WHERE {where}",
            "one row per trip, stop and moment",
            maximum=0,
        )
    )
    out.append(
        CheckSpec(
            "ml_examples_rows",
            f"SELECT count(*) FROM {t} WHERE {where}",
            "a full weekday gives about 1.2 million rows (1.2 million on 2026-10-20); a collapse means a join broke",
            minimum=300000,
        )
    )
    return out


def incidents(prefix: str) -> list[CheckSpec]:
    """Incidents are fetched every 2 hours and kept once each."""

    inc = _t(prefix, "silver", "incidents")
    return [
        CheckSpec(
            "incidents_hours_since_newest_pull",
            f"SELECT (unix_timestamp(current_timestamp()) - unix_timestamp(max(pulled_at))) / 3600.0 FROM {inc}",
            "Seattle Fire dispatches are fetched every 2 hours (GitHub workflow incidents.yml), each time the last 30 hours; older than 26 hours means data is being lost",
            maximum=26.0,
        ),
        CheckSpec(
            "incidents_duplicate_numbers",
            f"SELECT count(*) - count(DISTINCT incident_number) FROM {inc}",
            "fetches overlap, so repeats must be dropped: one row per incident",
            maximum=0,
        ),
    ]


def events(prefix: str) -> list[CheckSpec]:
    """The daily event pull is fresh, and the event tables have one row per event (and route)."""

    ev, er = _t(prefix, "silver", "events"), _t(prefix, "gold", "event_routes")
    return [
        CheckSpec(
            "events_hours_since_newest_pull",
            f"SELECT (unix_timestamp(current_timestamp()) - unix_timestamp(max(pulled_at))) / 3600.0 FROM {ev}",
            "events are pulled from Ticketmaster once a day (GitHub workflow events.yml); older than 50 hours means two pulls were missed",
            maximum=50.0,
        ),
        CheckSpec(
            "events_duplicate_ids",
            f"SELECT count(*) - count(DISTINCT event_id) FROM {ev}",
            "silver keeps the newest version of each event, so each event appears once",
            maximum=0,
        ),
        CheckSpec(
            "venues_duplicate_ids",
            f"SELECT count(*) - count(DISTINCT venue_id) FROM {_t(prefix, 'silver', 'venues')}",
            "one row per venue",
            maximum=0,
        ),
        CheckSpec(
            "event_routes_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT event_id, route_id) FROM {er}",
            "one row per event and route",
            maximum=0,
        ),
    ]


def weather(prefix: str) -> list[CheckSpec]:
    """Weather tables are unique per station and time, and the impact table matches an independent recount."""

    obs, hourly = (
        _t(prefix, "silver", "weather_observations"),
        _t(prefix, "silver", "weather_hourly"),
    )
    tp, wi = _t(prefix, "gold", "trip_performance"), _t(prefix, "gold", "weather_impact")
    start = "from_utc_timestamp(t.scheduled_start, 'America/Los_Angeles')"
    return [
        CheckSpec(
            "weather_observation_duplicate_keys",
            f"SELECT count(*) - count(DISTINCT station, observed_ts) FROM {obs}",
            "each hourly fetch overlaps the last, so duplicates must be removed: one row per station and observation time",
            maximum=0,
        ),
        CheckSpec(
            "weather_hours_without_observations",
            f"SELECT count(*) FROM {hourly} WHERE n_obs < 1 OR n_stations < 1",
            "an hour row exists only because observations exist",
            maximum=0,
        ),
        CheckSpec(
            "weather_impact_scored_equals_independent_recount",
            (
                f"SELECT abs((SELECT sum(n_scored) FROM {wi}) - (SELECT sum(t.n_rider_early + t.n_rider_on_time + t.n_rider_late + t.n_rider_uncertain) FROM {tp} t "
                f"WHERE hour({start}) >= 4 AND EXISTS (SELECT 1 FROM {hourly} w WHERE w.n_obs >= 6 AND w.pt_date = to_date({start}) AND w.pt_hour = hour({start}))))"
            ),
            "every scored timepoint of a trip that started in an hour with weather is counted exactly once in the impact table (recounted with EXISTS instead of a join)",
            maximum=0,
        ),
    ]


def all_checks(prefix: str, day: str) -> list[CheckSpec]:
    return [replace(c, severity="warn") if c.name in KNOWN_LIMITS else c for c in _all(prefix, day)]


def _all(prefix: str, day: str) -> list[CheckSpec]:
    return [
        *bronze_to_silver(prefix),
        *schedule(prefix, day),
        *stop_visits(prefix, day),
        *coverage(prefix, day),
        *gold(prefix, day),
        *segments(prefix, day),
        *headways(prefix, day),
        *weather(prefix),
        *events(prefix),
        *incidents(prefix),
        *ml_examples(prefix, day),
        *delivery(prefix, day),
    ]
