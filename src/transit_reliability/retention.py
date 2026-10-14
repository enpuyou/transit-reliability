"""Decide which landed raw files could be deleted. Pure logic; this module never deletes.

A raw file is the only copy of what KCM published (the feeds overwrite themselves), and
bronze keeps only the fields the parser extracts. So a file is deletable only when all
three hold: it is older than the retention window, its path is recorded in bronze as a
`source_file`, and the silver tables for its date are verified (decision 0005).
"""

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

DT_FOLDER = re.compile(r"/dt=(\d{4}-\d{2}-\d{2})/")
COLLECTOR_NAME = re.compile(r"/\d+\.pb$")


@dataclass(frozen=True)
class RawFile:
    path: str
    size: int


@dataclass
class RetentionPlan:
    """Files grouped by why they can or cannot be deleted."""

    keep_recent: list[RawFile] = field(default_factory=list)
    blocked_not_in_bronze: list[RawFile] = field(default_factory=list)
    waiting_for_silver: list[RawFile] = field(default_factory=list)
    deletable: list[RawFile] = field(default_factory=list)
    ignored: list[RawFile] = field(default_factory=list)


def file_date(path: str) -> dt.date | None:
    match = DT_FOLDER.search(path)
    return dt.date.fromisoformat(match.group(1)) if match else None


def plan_retention(
    landed: Iterable[RawFile],
    ingested: set[str],
    today: dt.date,
    keep_days: int,
    silver_verified_through: dt.date | None,
) -> RetentionPlan:
    """Classify landed files. `ingested` holds the `source_file` values found in bronze."""

    cutoff = today - dt.timedelta(days=keep_days)
    plan = RetentionPlan()
    for raw in landed:
        day = file_date(raw.path)
        # Manual smoke files and anything without a dt= folder are never touched.
        if day is None or not COLLECTOR_NAME.search(raw.path):
            plan.ignored.append(raw)
        elif day >= cutoff:
            plan.keep_recent.append(raw)
        elif raw.path not in ingested:
            plan.blocked_not_in_bronze.append(raw)
        elif silver_verified_through is None or day > silver_verified_through:
            plan.waiting_for_silver.append(raw)
        else:
            plan.deletable.append(raw)
    return plan
