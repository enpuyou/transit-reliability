# ruff: noqa: E501  (SQL is kept on readable lines)
"""The baseline scoreboard: every predictor scored on exactly the same rows, the agency's way.

A "predictor" turns one training-table row into a predicted arrival time. The baselines here need no
training: the timetable, "stays as late as it is now" (Dublin's "delay" model), the historical average for
that stop and time of day (past days only), and the agency's own prediction. Models plug into the same
function later by adding their predictions as one more predictor.

Common rows: only rows where every predictor has an answer are scored, so nobody wins by skipping the hard
cases. That means the agency had a bus assigned and fresh (at most KCM_MAX_AGE_S seconds old), the trip was
not canceled, history has at least MIN_HISTORY_N past observations (a day type needs an earlier day of the same
type, so the first weekend is out), and the service date is complete (yesterday or earlier).

For each predictor the error is `actual - predicted` (negative = the bus came early) and the "minutes away"
is the predictor's OWN predicted time minus the moment (as in WMATA's definition). The daily sums let
`scoring.bootstrap_by_day` give intervals that resample whole days.
"""

from transit_reliability.ml.scoring import bucket_sql, window_sql

KCM_MAX_AGE_S = 120
MIN_HISTORY_N = (
    5  # a historical average of fewer past observations is too thin to count as a predictor
)
# a service date is complete once its after-midnight trips have ended: yesterday and earlier
COMPLETE_DAY = (
    "date_sub(to_date(from_utc_timestamp(current_timestamp(), 'America/Los_Angeles')), 1)"
)
PREDICTORS = {
    "timetable": "sched",
    "persistence": "sched + delay_now_s",
    "history": "sched + hist_delay_mean_s",
    "agency": "kcm_pred",
}


def scoreboard_sql(examples_table: str, extra_predictors: dict[str, str] | None = None) -> str:
    """Daily sums per predictor and window, from a training table (or a table plus model predictions)."""

    preds = {**PREDICTORS, **(extra_predictors or {})}
    union = "\nUNION ALL\n".join(
        f"SELECT service_date, '{name}' AS predictor, t, actual, {expr} AS pred FROM base"
        for name, expr in preds.items()
    )
    return f"""
    WITH base AS (
      SELECT service_date, CAST(as_of AS DOUBLE) AS t, CAST(actual_ts AS DOUBLE) AS actual, CAST(sched_ts AS DOUBLE) AS sched,
             delay_now_s, hist_delay_mean_s, CAST(kcm_pred_ts AS DOUBLE) AS kcm_pred
      FROM {examples_table}
      WHERE hist_delay_mean_s IS NOT NULL AND hist_n >= {MIN_HISTORY_N} AND kcm_has_bus AND NOT kcm_canceled
        AND kcm_pred_ts IS NOT NULL AND kcm_age_s <= {KCM_MAX_AGE_S} AND service_date <= {COMPLETE_DAY}),
    preds AS ({union}),
    scored AS (
      SELECT service_date, predictor, actual - pred AS err_s, actual - t AS to_go_s,
             {bucket_sql("(pred - t) / 60.0")} AS bucket, {window_sql("(pred - t) / 60.0", "actual - pred")} AS in_window
      FROM preds)
    SELECT service_date, predictor, bucket, count(*) AS n, sum(in_window) AS n_in_window,
           sum(abs(err_s)) AS sum_abs_err, sum(err_s * err_s) AS sum_sq_err, sum(err_s) AS sum_err,
           sum(abs(err_s) / to_go_s) AS sum_abs_pct_err
    FROM scored WHERE bucket IS NOT NULL AND to_go_s > 0 GROUP BY service_date, predictor, bucket"""


def build_baseline_scores(spark: object, prefix: str) -> None:
    """Rewrite `<prefix>_ml.baseline_scores_daily` from the training table (small: a few thousand rows)."""

    target = f"workspace.{prefix}_ml.baseline_scores_daily"
    source = f"workspace.{prefix}_ml.arrival_examples"
    spark.sql(f"CREATE OR REPLACE TABLE {target} AS {scoreboard_sql(source)}")  # type: ignore[attr-defined]
    n = spark.table(target).count()  # type: ignore[attr-defined]
    print(f"{target}: {n} rows")
