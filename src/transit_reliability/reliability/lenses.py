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


# The HEADLINE RULE (owner, 2026-10-20, decision 0021): the "rider" rule. It uses the headline
# window above but judges each side where it matters to a rider: EARLY on the departure (a bus that
# leaves early is a missed bus; arriving early and holding is normal) and LATE on the arrival (that
# is when the rider is late). The arrival-only lenses above stay available for comparison.
HEADLINE_RULE = "rider"


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


Interval = tuple[float | None, float | None]


def classify_rider(
    arrival: Interval,
    departure: Interval,
    *,
    first_stop: bool,
    last_stop: bool,
    window: Lens | None = None,
) -> str | None:
    """The rider rule for one timepoint: early, on_time, late, uncertain, or None (not scored).

    Delays are in seconds relative to the schedule, each a [lo, hi] interval (or (None, None) when
    that time could not be estimated).

    - The last stop is not scored (it has no departure, and its arrival is rarely measurable).
    - early: the departure is entirely earlier than the early limit.
    - late: the arrival is entirely later than the late limit; at the FIRST stop the bus is parked
      and its arrival is not measurable, so the departure is used.
    - on time: certainly not early and certainly not late. A bus cannot leave before it arrives,
      so a lower bound of either time is a lower bound of the departure, and an upper bound of
      either is an upper bound of the arrival; whichever was measured can certify.
    - uncertain: otherwise. A timepoint with neither time estimated is not scored.
    """

    if last_stop:
        return None
    w = window or headline()
    arr_lo, arr_hi = arrival
    dep_lo, dep_hi = departure
    # Each bound is evidence on its own: a bus still seen at the stop gives a departure LOWER bound
    # with no upper bound (it left no earlier than that), which can prove "late" at the first stop
    # and "not early" anywhere, but can never prove "on time" alone.
    if arr_lo is None and dep_lo is None:
        return None
    if dep_hi is not None and dep_hi < w.early_s:
        return "early"
    late_bound = dep_lo if first_stop else arr_lo
    if late_bound is not None and late_bound > w.late_s:
        return "late"
    lows = [x for x in (arr_lo, dep_lo) if x is not None]
    highs = [x for x in (arr_hi, dep_hi) if x is not None]
    if lows and highs and max(lows) >= w.early_s and min(highs) <= w.late_s:
        return "on_time"
    return "uncertain"
