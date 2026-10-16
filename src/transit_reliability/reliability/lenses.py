"""On-time "lenses": named windows around the schedule, applied to times that carry an interval.

There is no national standard for how early or late still counts as on time (agencies use windows
from "1 minute early to 4 minutes late" to "2 early to 7 late"; Metro's own current definition is
unverified here). So the project keeps the exact delay of every stop visit and classifies under
several lenses side by side. Change the list below to add or tune one; nothing else depends on it.

A stop visit's time is an interval, not an instant. Classifying by the interval keeps the error
visible: a visit is only called on time, early, or late when the *whole* interval is, and otherwise
it is "uncertain" and reported separately.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lens:
    name: str
    early_s: float  # a delay below this many seconds (a negative number) is early
    late_s: float  # a delay above this many seconds is late


LENSES: tuple[Lens, ...] = (
    Lens("strict", -60.0, 180.0),  # 1 minute early to 3 minutes late: a rider-facing view
    Lens("common", -60.0, 300.0),  # 1 minute early to 5 minutes late: the widely used convention
    Lens("metro_like", -90.0, 330.0),  # 1.5 early to 5.5 late: reported for Metro (UNVERIFIED)
)


# The lens used for headline figures. The others stay available for comparison. Chosen by the owner
# on 2026-10-16: the widely used 1 minute early to 5 minutes late window, which also makes figures
# comparable with other agencies. Tighter and looser windows are shown alongside, never instead.
HEADLINE_LENS = "common"


def headline() -> Lens:
    return next(lens for lens in LENSES if lens.name == HEADLINE_LENS)


def classify(delay_lo: float | None, delay_hi: float | None, lens: Lens) -> str | None:
    """early, on_time, late, or uncertain for a delay known to lie in [delay_lo, delay_hi]."""

    if delay_lo is None or delay_hi is None:
        return None
    if delay_hi < lens.early_s:
        return "early"
    if delay_lo > lens.late_s:
        return "late"
    if delay_lo >= lens.early_s and delay_hi <= lens.late_s:
        return "on_time"
    return "uncertain"
