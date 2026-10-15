"""Run-length folding of time-ordered snapshots. A pure reference for the Spark silver build.

A *run* is a stretch of consecutive snapshots with the same value. Silver keeps one row per
run (first_seen, last_seen, snapshots) instead of one row per snapshot. Because new snapshots
arrive in batches, the run that was open at the end of the last batch may continue in the next
one: `extend_runs` takes that open run as `prior` and returns only the runs that were created
or extended, so a MERGE on (key, first_seen) can update or insert them.
"""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, replace

# A prediction not seen for longer than this starts a new run, even if its value is unchanged.
# Without it, whether a long silence splits a run would depend on how the work was batched.
MAX_GAP_SECONDS = 3600


@dataclass(frozen=True)
class Run:
    value: tuple[Hashable, ...]
    first_seen: int
    last_seen: int
    snapshots: int


def extend_runs(
    prior: Run | None,
    snapshots: Iterable[tuple[int, tuple[Hashable, ...]]],
    max_gap: int | None = None,
) -> list[Run]:
    """Fold (timestamp, value) snapshots into runs, continuing `prior`; return what changed.

    Snapshots at or before `prior.last_seen` were already folded in and are ignored, which
    makes overlapping reads (the safety lookback) harmless. Only the first returned run can be
    `prior` extended; all others are new. With `max_gap`, a silence longer than that many seconds
    between consecutive snapshots ends the run.
    """

    out: list[Run] = []
    open_run = prior
    touched = False
    for ts, value in sorted(snapshots, key=lambda s: s[0]):
        if prior is not None and ts <= prior.last_seen:
            continue
        continues = open_run is not None and open_run.value == value
        if continues and max_gap is not None and open_run is not None:
            continues = ts - open_run.last_seen <= max_gap
        if open_run is not None and continues:
            open_run = replace(open_run, last_seen=ts, snapshots=open_run.snapshots + 1)
        else:
            if open_run is not None and touched:
                out.append(open_run)
            open_run = Run(value, ts, ts, 1)
        touched = True
    if open_run is not None and touched:
        out.append(open_run)
    return out
