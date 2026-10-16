"""Score the agency's arrival predictions against the arrivals we inferred. Pure reference.

For a stop the bus reached at time T and a horizon H, the prediction "H minutes ahead" is the latest
prediction published at or before T - H (strictly no later information). It counts only if a bus was
assigned to the trip at that moment and had already been seen on it; before that the published
"prediction" is just the timetable (decision 0010). Canceled trips and predictions with no arrival
time are skipped, and a prediction that had gone quiet for too long before the horizon is not used.

Three predictors are scored on identical rows so they can be compared fairly:
- the agency's prediction,
- the schedule alone (predict zero delay),
- persistence (the bus's most recent observed delay carries on to the target stop).

Error = predicted arrival minus actual arrival (seconds): positive means predicted too late. The
"actual" is itself an estimate from GPS (typically within about 15 s), so differences much smaller
than that are not meaningful.
"""

from collections.abc import Sequence
from dataclasses import dataclass

HORIZONS_MIN = (5, 15, 30)
MAX_STALE_S = 120.0  # an as-of prediction last confirmed longer ago than this is treated as missing


@dataclass(frozen=True)
class ObservedVisit:
    stop_sequence: int
    sched_arr: float | None  # scheduled arrival, epoch seconds
    actual: float  # inferred arrival, epoch seconds
    delay_s: float | None  # actual minus scheduled


@dataclass(frozen=True)
class PredRun:
    stop_sequence: int
    first_seen: float
    last_seen: float
    arrival_time: float | None  # the predicted arrival, epoch seconds
    assigned: bool  # a vehicle was attached to the trip in this prediction
    canceled: bool


@dataclass(frozen=True)
class ErrorRow:
    stop_sequence: int
    horizon_min: int
    as_of: float
    actual: float
    kcm_error: float
    sched_error: float
    persistence_error: float


def score_trip(
    visits: Sequence[ObservedVisit],
    runs: Sequence[PredRun],
    first_fix: float | None,
    horizons: Sequence[int] = HORIZONS_MIN,
) -> list[ErrorRow]:
    """Errors for every (observed stop, horizon) where all three predictors are available."""

    if first_fix is None:
        return []
    out: list[ErrorRow] = []
    for v in visits:
        if v.sched_arr is None:
            continue
        stop_runs = sorted(
            (r for r in runs if r.stop_sequence == v.stop_sequence), key=lambda r: r.first_seen
        )
        for horizon in horizons:
            t = v.actual - horizon * 60.0
            if first_fix > t:
                continue
            candidates = [r for r in stop_runs if r.first_seen <= t]
            if not candidates:
                continue
            latest = candidates[-1]
            if (
                not latest.assigned
                or latest.canceled
                or latest.arrival_time is None
                or t - latest.last_seen > MAX_STALE_S
            ):
                continue
            known = [
                p
                for p in visits
                if p.stop_sequence < v.stop_sequence and p.delay_s is not None and p.actual <= t
            ]
            if not known:
                continue
            last_known = max(known, key=lambda p: p.actual)
            assert last_known.delay_s is not None
            out.append(
                ErrorRow(
                    v.stop_sequence,
                    horizon,
                    t,
                    v.actual,
                    latest.arrival_time - v.actual,
                    v.sched_arr - v.actual,
                    (v.sched_arr + last_known.delay_s) - v.actual,
                )
            )
    return out
