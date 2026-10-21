# ruff: noqa: E501  (SQL is kept on readable single lines so it can be pasted into the editor)
"""SQL that builds the `<prefix>_public` tables. Aggregates only, plus a few masked sample rows.

Two groups, so the hourly job stays light and the daily job does the heavy scans:
- LIVE (hourly): snapshots per hour, check results, job runs;
- HEAVY (daily): daily counts per stage, check trends, lineage, billing, distributions.

Privacy by construction: no query here selects a raw vehicle id or exact coordinates. The sample
queries hash the id and round coordinates to two decimals (about 1 km); `tests/test_public.py`
fails if that ever changes.
"""

from collections.abc import Callable

PACIFIC_TODAY = "to_date(from_utc_timestamp(current_timestamp(), 'America/Los_Angeles'))"
COMPLETE_DAY = f"date_sub({PACIFIC_TODAY}, 2)"  # a service date is complete two days later
BRONZE_SOURCES = ("vehicle_positions", "trip_updates", "alerts")
PACIFIC_ZONE = "'America/Los_Angeles'"
PROD_JOB_NAMES = "j.job_name LIKE 'transit-reliability-prod-%'"
JOB_NAMES = (
    "SELECT job_id, max_by(name, change_time) AS job_name FROM system.lakeflow.jobs GROUP BY job_id"
)


def pacific_date(expr: str) -> str:
    """The calendar day in Seattle of a UTC timestamp expression (day buckets match what a rider sees)."""

    return f"to_date(from_utc_timestamp({expr}, {PACIFIC_ZONE}))"


def public_schema(prefix: str) -> str:
    return f"workspace.{prefix}_public"


def _table(prefix: str, layer: str) -> Callable[[str], str]:
    return lambda name: f"workspace.{prefix}_{layer}.{name}"


def live_queries(prefix: str) -> dict[str, str]:
    bronze, ops = _table(prefix, "bronze"), _table(prefix, "ops")
    snapshots = " UNION ALL ".join(
        f"SELECT '{s}' AS source, date_trunc('HOUR', timestamp_seconds(feed_header_ts)) AS hour, count(DISTINCT feed_header_ts) AS snapshots FROM {bronze(s)} WHERE feed_header_ts >= unix_timestamp(current_timestamp() - INTERVAL 48 HOURS) GROUP BY 1, 2"
        for s in BRONZE_SOURCES
    )
    return {
        "hourly_snapshots": snapshots,
        "check_latest": (
            "SELECT suite, check_name, status, value, rule, severity, explanation, sql, run_ts FROM "
            f"(SELECT *, row_number() OVER (PARTITION BY suite, check_name ORDER BY run_ts DESC) AS rn FROM {ops('check_results')}) WHERE rn = 1"
        ),
        "check_values_hourly": (
            f"SELECT run_ts, check_name, status, value FROM {ops('check_results')} "
            "WHERE suite = 'hourly' AND run_ts >= current_timestamp() - INTERVAL 72 HOURS"
        ),
        "job_runs": (
            f"WITH j AS ({JOB_NAMES}) "
            "SELECT j.job_name, t.run_id, min(t.period_start_time) AS start_time, max(t.period_end_time) AS end_time, "
            "max_by(t.result_state, t.period_end_time) FILTER (WHERE t.result_state IS NOT NULL) AS result_state, "
            "any_value(t.trigger_type) AS trigger_type "
            "FROM system.lakeflow.job_run_timeline t JOIN j ON j.job_id = t.job_id "
            f"WHERE t.period_start_time >= current_timestamp() - INTERVAL 7 DAYS AND {PROD_JOB_NAMES} GROUP BY j.job_name, t.run_id"
        ),
        "job_task_runs": (
            f"WITH j AS ({JOB_NAMES}) "
            "SELECT j.job_name, t.task_key, t.job_run_id AS run_id, min(t.period_start_time) AS start_time, max(t.period_end_time) AS end_time, "
            "max_by(t.result_state, t.period_end_time) FILTER (WHERE t.result_state IS NOT NULL) AS result_state "
            "FROM system.lakeflow.job_task_run_timeline t JOIN j ON j.job_id = t.job_id "
            f"WHERE t.period_start_time >= current_timestamp() - INTERVAL 7 DAYS AND {PROD_JOB_NAMES} GROUP BY j.job_name, t.task_key, t.job_run_id"
        ),
    }


def _daily_metric_parts(prefix: str) -> list[str]:
    bronze, silver, gold = (
        _table(prefix, "bronze"),
        _table(prefix, "silver"),
        _table(prefix, "gold"),
    )
    since = "feed_header_ts >= unix_timestamp(current_timestamp() - INTERVAL 8 DAYS)"
    recent = f"service_date >= date_sub({PACIFIC_TODAY}, 8)"
    day = pacific_date("timestamp_seconds(feed_header_ts)")
    parts = []
    for s in BRONZE_SOURCES:
        parts.append(
            f"SELECT {day} AS day, 'bronze.{s}' AS stage, 'distinct files' AS metric, CAST(count(DISTINCT source_file) AS DOUBLE) AS value FROM {bronze(s)} WHERE {since} GROUP BY 1 "
            f"UNION ALL SELECT {day}, 'bronze.{s}', 'distinct snapshots', CAST(count(DISTINCT feed_header_ts) AS DOUBLE) FROM {bronze(s)} WHERE {since} GROUP BY 1 "
            f"UNION ALL SELECT {day}, 'bronze.{s}', 'rows', CAST(count(*) AS DOUBLE) FROM {bronze(s)} WHERE {since} GROUP BY 1"
        )
    parts += [
        f"SELECT {pacific_date('vehicle_ts')} AS day, 'silver.vehicle_positions' AS stage, 'distinct fixes' AS metric, CAST(count(*) AS DOUBLE) AS value FROM {silver('vehicle_positions')} WHERE vehicle_ts >= current_timestamp() - INTERVAL 8 DAYS GROUP BY 1",
        f"SELECT {pacific_date('bronze_ingested_at')}, 'silver.quarantine_vehicle_positions', 'rejected rows', CAST(count(*) AS DOUBLE) FROM {silver('quarantine_vehicle_positions')} WHERE bronze_ingested_at >= current_timestamp() - INTERVAL 8 DAYS GROUP BY 1",
        f"SELECT service_date, 'silver.scheduled_trips', 'scheduled trips', CAST(count(*) AS DOUBLE) FROM {silver('scheduled_trips')} WHERE {recent} GROUP BY 1",
        f"SELECT service_date, 'silver.stop_visits', 'scheduled stops', CAST(count(*) AS DOUBLE) FROM {silver('stop_visits')} WHERE {recent} GROUP BY 1",
        f"SELECT service_date, 'silver.stop_visits', 'stops with a measured arrival', CAST(count(arr_est) AS DOUBLE) FROM {silver('stop_visits')} WHERE {recent} GROUP BY 1",
        f"SELECT service_date, 'gold.trip_performance', concat('trips ', trip_status), CAST(count(*) AS DOUBLE) FROM {gold('trip_performance')} WHERE {recent} GROUP BY service_date, trip_status",
    ]
    return parts


def _distribution_parts(prefix: str) -> list[str]:
    silver, gold = _table(prefix, "silver"), _table(prefix, "gold")
    sv, tp, pe = silver("stop_visits"), gold("trip_performance"), gold("prediction_errors")
    cd = f"service_date = {COMPLETE_DAY}"
    delay_bin = "least(greatest(floor(arr_delay_s / 60), -10), 20)"
    width = "unix_timestamp(arr_hi) - unix_timestamp(arr_lo)"
    pt_hour = "hour(from_utc_timestamp(sched_arr, 'America/Los_Angeles'))"
    parts = [
        f"SELECT 'delay_histogram_minutes' AS name, {COMPLETE_DAY} AS day, CAST({delay_bin} AS STRING) AS bucket, CAST({delay_bin} AS INT) AS bucket_order, CAST(count(*) AS DOUBLE) AS value FROM {sv} WHERE {cd} AND timepoint = 1 AND arr_delay_s IS NOT NULL GROUP BY 3, 4",
        f"SELECT 'arrival_method_mix', {COMPLETE_DAY}, arr_method, 0, CAST(count(*) AS DOUBLE) FROM {sv} WHERE {cd} GROUP BY 3",
        f"SELECT 'interval_width_seconds', {COMPLETE_DAY}, CASE WHEN {width} = 0 THEN '0 (exact)' WHEN {width} <= 20 THEN '1-20' WHEN {width} <= 60 THEN '21-60' WHEN {width} <= 180 THEN '61-180' ELSE 'over 180' END, "
        f"CASE WHEN {width} = 0 THEN 0 WHEN {width} <= 20 THEN 1 WHEN {width} <= 60 THEN 2 WHEN {width} <= 180 THEN 3 ELSE 4 END, CAST(count(*) AS DOUBLE) FROM {sv} WHERE {cd} AND arr_est IS NOT NULL GROUP BY 3, 4",
        f"SELECT 'coverage_by_pacific_hour', {COMPLETE_DAY}, CAST({pt_hour} AS STRING), {pt_hour}, avg(CASE WHEN arr_est IS NOT NULL THEN 1.0 ELSE 0.0 END) FROM {sv} WHERE {cd} GROUP BY 3, 4",
        f"SELECT 'fixes_per_trip', {COMPLETE_DAY}, CASE WHEN n = 0 THEN 'none' WHEN n <= 20 THEN '1-20' WHEN n <= 60 THEN '21-60' WHEN n <= 120 THEN '61-120' WHEN n <= 240 THEN '121-240' ELSE 'over 240' END, "
        f"CASE WHEN n = 0 THEN 0 WHEN n <= 20 THEN 1 WHEN n <= 60 THEN 2 WHEN n <= 120 THEN 3 WHEN n <= 240 THEN 4 ELSE 5 END, CAST(count(*) AS DOUBLE) FROM (SELECT max(n_fixes) AS n FROM {sv} WHERE {cd} GROUP BY trip_id) GROUP BY 3, 4",
    ]
    lead = (
        "CASE WHEN pointer_lead_ft <= 100 THEN 0 WHEN pointer_lead_ft <= 200 THEN 1 WHEN pointer_lead_ft <= 500 THEN 2 "
        "WHEN pointer_lead_ft <= 1000 THEN 3 WHEN pointer_lead_ft <= 2000 THEN 4 ELSE 5 END"
    )
    parts.append(
        f"SELECT 'pointer_lead_feet', {COMPLETE_DAY}, CASE b WHEN 0 THEN '0-100' WHEN 1 THEN '101-200' WHEN 2 THEN '201-500' WHEN 3 THEN '501-1000' WHEN 4 THEN '1001-2000' ELSE 'over 2000' END, b, CAST(count(*) AS DOUBLE) "
        f"FROM (SELECT {lead} AS b FROM {sv} WHERE {cd} AND pointer_lead_ft IS NOT NULL) GROUP BY 3, 4"
    )
    sr, rt = gold("segment_runs"), gold("segment_runtimes")
    win = f"service_date BETWEEN date_sub({COMPLETE_DAY}, 27) AND {COMPLETE_DAY} AND day_type = 'weekday'"
    order = "CASE band WHEN 'am_peak' THEN 0 WHEN 'midday' THEN 1 WHEN 'pm_peak' THEN 2 WHEN 'evening' THEN 3 ELSE 4 END"
    parts += [
        f"SELECT 'segment_excess_by_band', {COMPLETE_DAY}, band, {order}, percentile(obs_s - sched_s, 0.5) FROM {sr} WHERE {win} GROUP BY band",
        f"SELECT 'segment_share_over_by_band', {COMPLETE_DAY}, band, {order}, avg(CASE WHEN obs_s > sched_s THEN 1.0 ELSE 0.0 END) FROM {sr} WHERE {win} GROUP BY band",
        f"SELECT 'segment_excess_histogram', {COMPLETE_DAY}, CASE o WHEN 0 THEN 'over 2 min faster' WHEN 1 THEN '1-2 min faster' WHEN 2 THEN 'up to 1 min faster' WHEN 3 THEN 'up to 1 min slower' WHEN 4 THEN '1-2 min slower' ELSE 'over 2 min slower' END, o, CAST(count(*) AS DOUBLE) "
        f"FROM (SELECT CASE WHEN excess_p50_s < -120 THEN 0 WHEN excess_p50_s < -60 THEN 1 WHEN excess_p50_s < 0 THEN 2 WHEN excess_p50_s < 60 THEN 3 WHEN excess_p50_s < 120 THEN 4 ELSE 5 END AS o FROM {rt} WHERE day_type = 'weekday' AND n >= 30) GROUP BY o",
    ]
    hp = gold("headway_pairs")
    hwin = f"frequent AND service_date BETWEEN date_sub({COMPLETE_DAY}, 27) AND {COMPLETE_DAY} AND day_type = 'weekday'"
    border = "CASE band WHEN 'am_peak' THEN 0 WHEN 'midday' THEN 1 WHEN 'pm_peak' THEN 2 WHEN 'evening' THEN 3 ELSE 4 END"
    for cls in ("bunched", "gapped", "regular", "uncertain"):
        parts.append(
            f"SELECT 'headway_outcomes_by_band', {COMPLETE_DAY}, concat(band, '|{cls}'), {border}, CAST(count(*) AS DOUBLE) FROM {hp} WHERE {hwin} AND outcome = '{cls}' GROUP BY band"
        )
    wh, wi = silver("weather_hourly"), gold("weather_impact")
    border2 = "CASE band WHEN 'am_peak' THEN 0 WHEN 'midday' THEN 1 WHEN 'pm_peak' THEN 2 WHEN 'evening' THEN 3 ELSE 4 END"
    parts += [
        f"SELECT 'weather_hours_by_class', {COMPLETE_DAY}, weather_class, CASE weather_class WHEN 'dry' THEN 0 WHEN 'fog' THEN 1 ELSE 2 END, CAST(count(*) AS DOUBLE) FROM {wh} WHERE n_obs >= 6 GROUP BY weather_class",
        f"SELECT 'weather_impact_on_time_share', {COMPLETE_DAY}, concat(band, '|', weather_class), {border2}, sum(n_on_time) / sum(n_scored) FROM {wi} WHERE day_type = 'weekday' GROUP BY band, weather_class",
        f"SELECT 'weather_impact_hours', {COMPLETE_DAY}, concat(band, '|', weather_class), {border2}, CAST(sum(n_hours) AS DOUBLE) FROM {wi} WHERE day_type = 'weekday' GROUP BY band, weather_class",
    ]
    for outcome in ("early", "on_time", "late", "uncertain"):
        for name, column in (
            ("on_time_by_day_common_lens", "n_common"),  # the arrival-only view
            ("on_time_by_day_rider_rule", "n_rider"),  # the headline (decision 0021)
        ):
            parts.append(
                f"SELECT '{name}', service_date, '{outcome}', 0, CAST(sum({column}_{outcome}) AS DOUBLE) FROM {tp} WHERE service_date BETWEEN date_sub({COMPLETE_DAY}, 6) AND {COMPLETE_DAY} GROUP BY 2"
            )
    for source, column in (
        ("kcm", "kcm_error_s"),
        ("schedule", "sched_error_s"),
        ("persistence", "persistence_error_s"),
    ):
        parts.append(
            f"SELECT 'prediction_median_abs_error_seconds', {COMPLETE_DAY}, concat('{source} ', CAST(horizon_min AS STRING), ' min'), horizon_min, percentile(abs({column}), 0.5) FROM {pe} "
            f"WHERE service_date BETWEEN date_sub({COMPLETE_DAY}, 2) AND {COMPLETE_DAY} AND width_s <= 60 AND abs(kcm_error_s) < 3600 GROUP BY 3, 4"
        )
    return parts


def heavy_queries(prefix: str) -> dict[str, str]:
    ops = _table(prefix, "ops")
    owned = f"startswith(source_table_full_name, 'workspace.{prefix}_') AND startswith(target_table_full_name, 'workspace.{prefix}_')"
    not_public = f"NOT startswith(source_table_full_name, 'workspace.{prefix}_public.') AND NOT startswith(target_table_full_name, 'workspace.{prefix}_public.')"
    return {
        "daily_metrics": " UNION ALL ".join(_daily_metric_parts(prefix)),
        "check_trend": (
            f"SELECT {pacific_date('run_ts')} AS day, suite, check_name, count(*) AS runs, count_if(status = 'fail') AS failures, count_if(status = 'warn') AS warnings, min(value) AS min_value, max(value) AS max_value "
            f"FROM {ops('check_results')} WHERE run_ts >= current_timestamp() - INTERVAL 14 DAYS GROUP BY 1, 2, 3"
        ),
        "lineage_edges": (
            "SELECT DISTINCT source_table_full_name AS source_table, target_table_full_name AS target_table FROM system.access.table_lineage "
            f"WHERE event_date >= date_sub(current_date(), 30) AND {owned} AND {not_public} AND source_table_full_name <> target_table_full_name"
        ),
        "billing_daily": (
            f"WITH j AS ({JOB_NAMES}) SELECT {pacific_date('u.usage_start_time')} AS usage_date, CASE "
            "WHEN j.job_name LIKE 'transit-reliability-prod-ingest' THEN 'ingest job (every 30 min)' "
            "WHEN j.job_name LIKE 'transit-reliability-prod-silver' THEN 'silver job (daily)' "
            "WHEN j.job_name LIKE 'transit-reliability-prod-monitor' THEN 'monitor job (hourly)' "
            "WHEN u.usage_metadata.job_id IS NOT NULL THEN 'development and one-off job runs' "
            "WHEN u.sku_name LIKE '%SQL%' THEN 'SQL warehouse (queries and checks)' "
            "ELSE 'other serverless compute' END AS label, round(sum(u.usage_quantity), 2) AS dbu "
            "FROM system.billing.usage u LEFT JOIN j ON j.job_id = u.usage_metadata.job_id WHERE u.usage_date >= date_sub(current_date(), 14) AND u.sku_name LIKE '%COMPUTE%' GROUP BY 1, 2"
        ),
        "distributions": " UNION ALL ".join(_distribution_parts(prefix)),
    }


def sample_queries(prefix: str) -> dict[str, tuple[str, str]]:
    """table name -> (layer, SQL returning one JSON string). Masked: see the module docstring."""

    bronze, silver, gold, ops = (_table(prefix, x) for x in ("bronze", "silver", "gold", "ops"))
    veh = "concat('veh-', substr(sha2(vehicle_id, 256), 1, 6)) AS vehicle_id"
    lat, lon = "round(latitude, 2) AS latitude", "round(longitude, 2) AS longitude"

    def newest_snapshot(table: str) -> str:
        return f"feed_header_ts = (SELECT max(feed_header_ts) FROM {table})"

    def latest_day(table: str) -> str:
        return f"service_date = (SELECT max(service_date) FROM {table})"

    vp, tu, al = bronze("vehicle_positions"), bronze("trip_updates"), bronze("alerts")
    svp, quar, tus = (
        silver("vehicle_positions"),
        silver("quarantine_vehicle_positions"),
        silver("trip_update_snapshots"),
    )
    st, sst, sv = silver("scheduled_trips"), silver("scheduled_stop_times"), silver("stop_visits")
    tp, rp, pe, pa = (
        gold("trip_performance"),
        gold("route_performance"),
        gold("prediction_errors"),
        gold("prediction_accuracy"),
    )
    cr = ops("check_results")
    sg, srt = gold("segment_runs"), gold("segment_runtimes")
    hp_, hs_ = gold("headway_pairs"), gold("headway_summary")
    wo_, wh_, wi_ = silver("weather_observations"), silver("weather_hourly"), gold("weather_impact")
    return {
        "bronze.vehicle_positions": (
            "bronze",
            f"SELECT to_json(struct({veh}, trip_id, route_id, {lat}, {lon}, current_stop_sequence, current_status, timestamp_seconds(vehicle_ts) AS vehicle_ts, feed_header_ts)) FROM {vp} WHERE {newest_snapshot(vp)} AND vehicle_id IS NOT NULL AND trip_id IS NOT NULL LIMIT 1",
        ),
        "bronze.trip_updates": (
            "bronze",
            f"SELECT to_json(struct(trip_id, route_id, stop_sequence, stop_id, arrival_delay, arrival_time, departure_delay, feed_header_ts)) FROM {tu} WHERE {newest_snapshot(tu)} AND arrival_delay IS NOT NULL LIMIT 1",
        ),
        "bronze.alerts": (
            "bronze",
            f"SELECT to_json(struct(cause, effect, header_text, feed_header_ts)) FROM {al} WHERE {newest_snapshot(al)} LIMIT 1",
        ),
        "silver.vehicle_positions": (
            "silver",
            f"SELECT to_json(struct({veh}, vehicle_ts, service_date, trip_id, route_id, {lat}, {lon}, current_stop_sequence, matched_trip)) FROM {svp} WHERE vehicle_ts = (SELECT max(vehicle_ts) FROM {svp}) LIMIT 1",
        ),
        "silver.quarantine_vehicle_positions": (
            "silver",
            f"SELECT to_json(struct(reason, {veh}, trip_id, {lat}, {lon})) FROM {quar} WHERE bronze_ingested_at = (SELECT max(bronze_ingested_at) FROM {quar}) LIMIT 1",
        ),
        "silver.trip_update_snapshots": (
            "silver",
            f"SELECT to_json(struct(trip_id, service_date, stop_sequence, arrival_delay, first_seen, last_seen, snapshots)) FROM {tus} WHERE last_seen = (SELECT max(last_seen) FROM {tus}) LIMIT 1",
        ),
        "silver.scheduled_trips": (
            "silver",
            f"SELECT to_json(struct(*)) FROM {st} WHERE {latest_day(st)} LIMIT 1",
        ),
        "silver.scheduled_stop_times": (
            "silver",
            f"SELECT to_json(struct(service_date, trip_id, route_id, stop_sequence, stop_id, scheduled_arrival_ts, scheduled_departure_ts, timepoint)) FROM {sst} WHERE {latest_day(sst)} LIMIT 1",
        ),
        "silver.stop_visits": (
            "silver",
            f"SELECT to_json(struct(service_date, trip_id, route_id, stop_sequence, stop_id, timepoint, sched_arr, arr_lo, arr_est, arr_hi, arr_method, arr_delay_s)) FROM {sv} WHERE {latest_day(sv)} AND arr_est IS NOT NULL LIMIT 1",
        ),
        "gold.trip_performance": (
            "gold",
            f"SELECT to_json(struct(service_date, trip_id, route_short_name, trip_headsign, trip_status, n_timepoints_measured, median_delay_s, p90_delay_s, n_rider_on_time, n_rider_late, n_rider_early, n_rider_uncertain, n_common_on_time, n_common_late, n_common_early, n_common_uncertain)) FROM {tp} WHERE {latest_day(tp)} AND trip_status = 'observed' LIMIT 1",
        ),
        "gold.route_performance": (
            "gold",
            f"SELECT to_json(struct(service_date, route_short_name, direction_id, start_hour_pt, n_trips, n_observed, n_common_on_time, n_common_late, n_common_early, n_common_uncertain)) FROM {rp} WHERE {latest_day(rp)} LIMIT 1",
        ),
        "gold.prediction_errors": (
            "gold",
            f"SELECT to_json(struct(service_date, trip_id, stop_sequence, horizon_min, as_of, actual_arrival, width_s, kcm_error_s, sched_error_s, persistence_error_s)) FROM {pe} WHERE {latest_day(pe)} LIMIT 1",
        ),
        "gold.segment_runs": (
            "gold",
            f"SELECT to_json(struct(service_date, trip_id, route_id, direction_id, from_stop_id, to_stop_id, sched_s, obs_s, obs_width_s, band, day_type, detour_alert)) FROM {sg} WHERE {latest_day(sg)} LIMIT 1",
        ),
        "gold.segment_runtimes": (
            "gold",
            f"SELECT to_json(struct(route_short_name, from_stop_name, to_stop_name, band, day_type, n, n_days, sched_median_s, obs_p50_s, obs_p85_s, excess_p50_s, share_over_schedule, median_uncertainty_s, detour_share)) FROM {srt} WHERE n >= 30 LIMIT 1",
        ),
        "gold.headway_pairs": (
            "gold",
            f"SELECT to_json(struct(service_date, route_id, direction_id, stop_id, trip_id, prev_trip_id, sched_h, act_h, act_lo, act_hi, frequent, outcome, band, day_type)) FROM {hp_} WHERE {latest_day(hp_)} AND frequent LIMIT 1",
        ),
        "gold.headway_summary": (
            "gold",
            f"SELECT to_json(struct(route_short_name, direction_id, band, day_type, n_pairs, n_days, n_bunched, n_gapped, n_regular, n_uncertain, sched_headway_median_s, ratio_p50, median_uncertainty_s)) FROM {hs_} WHERE n_pairs >= 30 LIMIT 1",
        ),
        "silver.weather_observations": (
            "silver",
            f"SELECT to_json(struct(station, observed_ts, temp_c, wind_kmh, visibility_m, humidity_pct, text, precip_level, fog, codes)) FROM {wo_} WHERE observed_ts = (SELECT max(observed_ts) FROM {wo_}) LIMIT 1",
        ),
        "silver.weather_hourly": (
            "silver",
            f"SELECT to_json(struct(hour_start, pt_date, pt_hour, n_obs, n_stations, temp_c_avg, wind_kmh_avg, visibility_m_min, wet_share, fog_share, weather_class)) FROM {wh_} WHERE hour_start = (SELECT max(hour_start) FROM {wh_}) LIMIT 1",
        ),
        "gold.weather_impact": (
            "gold",
            f"SELECT to_json(struct(weather_class, day_type, band, n_hours, n_trips, n_scored, n_on_time, n_late, n_early, n_uncertain, n_runs, median_excess_s)) FROM {wi_} WHERE day_type = 'weekday' AND band = 'midday' ORDER BY weather_class LIMIT 1",
        ),
        "gold.prediction_accuracy": (
            "gold",
            f"SELECT to_json(struct(*)) FROM {pa} WHERE {latest_day(pa)} LIMIT 1",
        ),
        "ops.check_results": (
            "ops",
            f"SELECT to_json(struct(run_ts, suite, check_name, status, value, rule, severity)) FROM {cr} WHERE run_ts = (SELECT max(run_ts) FROM {cr}) LIMIT 1",
        ),
    }
