"""How we score arrival predictions (the harness every model and baseline goes through).

Two scoring languages, decided before any model result (docs/ml_design.md section 4):

1. The agency window score (WMATA's published definition): a prediction counts as accurate when the
   bus arrives inside a window around the predicted time, and the window depends on how many minutes
   away the bus was predicted to be. After the first 3 minutes, early arrivals are judged more
   strictly than late ones, because a rider who misses an early bus is stranded.
2. The error measures the papers use: MAE, MAPE and RMSE, so reproduced numbers can be compared.

Error convention everywhere: `actual - predicted` in seconds. Negative means the bus came EARLIER
than predicted, positive means LATER.
"""

import math
import random
from collections.abc import Callable, Sequence

# (from minutes away, to minutes away, earliest tolerated seconds, latest tolerated seconds)
# WMATA bus prediction accuracy definition (2023 version of its dashboard definitions).
WMATA_WINDOWS: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 3.0, -60.0, 60.0),
    (3.0, 6.0, -90.0, 120.0),
    (6.0, 12.0, -150.0, 210.0),
    (12.0, 30.0, -240.0, 360.0),
)
BUCKETS = ("0-3", "3-6", "6-12", "12-30")


def bucket_index(minutes_away: float) -> int | None:
    """The window for a prediction made when the bus was `minutes_away` minutes from the stop.

    Predictions 30 or more minutes ahead, and negative ones (the predicted time had already
    passed), are not scored by this definition.
    """

    for i, (lo, hi, _, _) in enumerate(WMATA_WINDOWS):
        if lo <= minutes_away < hi:
            return i
    return None


def within_window(minutes_away: float, actual_minus_predicted_s: float) -> bool | None:
    """True if the arrival fell inside the window, False if outside, None if not scorable."""

    i = bucket_index(minutes_away)
    if i is None:
        return None
    _, _, early, late = WMATA_WINDOWS[i]
    return early <= actual_minus_predicted_s <= late


def window_sql(minutes_away: str, error_s: str) -> str:
    """The same rule as a Spark SQL expression: 1.0 inside, 0.0 outside, NULL if not scorable."""

    parts = " ".join(
        f"WHEN {minutes_away} >= {lo} AND {minutes_away} < {hi} "
        f"THEN CASE WHEN {error_s} BETWEEN {early} AND {late} THEN 1.0 ELSE 0.0 END"
        for lo, hi, early, late in WMATA_WINDOWS
    )
    return f"CASE {parts} END"


def bucket_sql(minutes_away: str) -> str:
    """The window name for a predicted minutes-away, as a Spark SQL expression."""

    parts = " ".join(
        f"WHEN {minutes_away} >= {lo} AND {minutes_away} < {hi} THEN '{name}'"
        for (lo, hi, _, _), name in zip(WMATA_WINDOWS, BUCKETS, strict=True)
    )
    return f"CASE {parts} END"


def mae(errors: Sequence[float]) -> float:
    return sum(abs(e) for e in errors) / len(errors)


def rmse(errors: Sequence[float]) -> float:
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def mape(errors: Sequence[float], times_to_arrival_s: Sequence[float]) -> float:
    """Mean absolute error as a percent of the real time still to go (the papers' MAPE)."""

    pairs = [(e, t) for e, t in zip(errors, times_to_arrival_s, strict=True) if t > 0]
    return 100.0 * sum(abs(e) / t for e, t in pairs) / len(pairs)


def quantile(values: Sequence[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))]


def interval_coverage(
    low: Sequence[float], high: Sequence[float], actual: Sequence[float]
) -> float:
    """Share of cases where the actual value fell inside the predicted range."""

    return sum(1 for lo, hi, a in zip(low, high, actual, strict=True) if lo <= a <= hi) / len(
        actual
    )


def bootstrap_by_day(
    daily: dict[str, tuple[float, float]], n: int = 2000, seed: int = 0, level: float = 0.95
) -> tuple[float, float, float]:
    """Confidence interval for a ratio (hits / total) by resampling whole days, not single rows.

    Rows from the same trip and day are not independent, so the day is the unit that is
    resampled. `daily` maps a day to (hits, total). Returns (estimate, low, high).
    """

    days = list(daily)
    hits = sum(h for h, _ in daily.values())
    total = sum(t for _, t in daily.values())
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        pick = [daily[rng.choice(days)] for _ in days]
        t = sum(p[1] for p in pick)
        stats.append(sum(p[0] for p in pick) / t if t else float("nan"))
    stats = sorted(s for s in stats if not math.isnan(s))
    lo = stats[int((1 - level) / 2 * len(stats))]
    hi = stats[min(len(stats) - 1, int((1 + level) / 2 * len(stats)))]
    return hits / total, lo, hi


def paired_gain_by_day(
    a: dict[str, tuple[float, float]],
    b: dict[str, tuple[float, float]],
    stat: Callable[[tuple[float, float]], float] = lambda p: p[0] / p[1],
    n: int = 2000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """How much better A is than B, with a day-resampled interval: (A - B, low, high).

    Both map a day to a pair of sums (for example hits and total). If the interval includes 0
    the difference is not clear from this much data.
    """

    days = [d for d in a if d in b]
    rng = random.Random(seed)

    def diff(sample: list[str]) -> float:
        ta = (sum(a[d][0] for d in sample), sum(a[d][1] for d in sample))
        tb = (sum(b[d][0] for d in sample), sum(b[d][1] for d in sample))
        return stat(ta) - stat(tb)

    diffs = sorted(diff([rng.choice(days) for _ in days]) for _ in range(n))
    return diff(days), diffs[int(0.025 * n)], diffs[min(n - 1, int(0.975 * n))]
