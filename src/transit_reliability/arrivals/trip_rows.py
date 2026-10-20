"""Turn one trip's route shape, fixes, and scheduled stops into output rows. Pure."""

from collections.abc import Sequence
from typing import Any

from transit_reliability.arrivals.infer import Fix, Shape, StopTarget, infer_stop_visits

# (ts, lat, lon, next_seq, stopped)
FixRow = tuple[float, float, float, int | None, bool]
# (stop_sequence, stop_id, dist, scheduled arrival epoch, scheduled departure epoch, timepoint)
StopRow = tuple[int, str, float, float | None, float | None, int | None]


def _delay(estimate: float | None, scheduled: float | None) -> float | None:
    return None if estimate is None or scheduled is None else estimate - scheduled


def stop_visit_rows(
    shape_points: Sequence[tuple[float, float, float]],
    fixes: Sequence[FixRow],
    stops: Sequence[StopRow],
) -> list[dict[str, Any]]:
    """One dict per scheduled stop, in stop order, with inferred times and delays.

    A trip with no fixes, or no usable shape, still yields a row per stop with a method that says
    why there is no estimate. Keeping those rows is deliberate: a scheduled visit that was never
    observed is itself a fact (service delivery), and silently dropping it would hide it.
    """

    targets = [StopTarget(s[0], s[1], s[2]) for s in stops]
    if len(shape_points) < 2:
        inferred = infer_stop_visits_without_shape(targets)
    else:
        inferred = infer_stop_visits(
            Shape(shape_points),
            [Fix(f[0], f[1], f[2], f[3], f[4]) for f in fixes],
            targets,
        )
    rows: list[dict[str, Any]] = []
    for stop, v in zip(stops, inferred, strict=True):
        sched_arr, sched_dep = stop[3], stop[4]
        rows.append(
            {
                "stop_sequence": stop[0],
                "stop_id": stop[1],
                "timepoint": stop[5],
                "sched_arr": sched_arr,
                "sched_dep": sched_dep,
                "arr_lo": v.arr_lo,
                "arr_hi": v.arr_hi,
                "arr_est": v.arr_est,
                "arr_method": v.arr_method,
                "dep_lo": v.dep_lo,
                "dep_hi": v.dep_hi,
                "dep_est": v.dep_est,
                "dep_method": v.dep_method,
                "arr_delay_s": _delay(v.arr_est, sched_arr),
                "dep_delay_s": _delay(v.dep_est, sched_dep),
                "advance_ts": v.advance_ts,
                "dwell_first": v.dwell_first,
                "dwell_last": v.dwell_last,
                "n_fixes": len(fixes),
                "pointer_lead_ft": v.pointer_lead_ft,
            }
        )
    return rows


def infer_stop_visits_without_shape(targets: Sequence[StopTarget]) -> list[Any]:
    from transit_reliability.arrivals.infer import StopVisit

    return [
        StopVisit(
            t.stop_sequence,
            t.stop_id,
            None,
            None,
            None,
            "no_shape",
            None,
            None,
            None,
            "no_shape",
            None,
            None,
            None,
        )  # fmt: skip
        for t in targets
    ]
