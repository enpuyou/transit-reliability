"""Pure pass/fail logic for the pipeline's health checks.

A job that finishes is not a job that worked: on 2026-10-16 to 18 the silver build reported
SUCCESS while keeping only the last hour of each day. These checks compare the output with what a
healthy pipeline must look like, and fail the task when it does not. A missing value fails: no data
must never look healthy.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    value: float | None
    minimum: float | None = None
    maximum: float | None = None


def failures(checks: Sequence[Check]) -> list[str]:
    """One message per failed check; empty when everything is healthy."""

    out: list[str] = []
    for c in checks:
        if c.value is None:
            out.append(f"{c.name}: no value")
        elif c.minimum is not None and c.value < c.minimum:
            out.append(f"{c.name}: {c.value} is below the minimum {c.minimum}")
        elif c.maximum is not None and c.value > c.maximum:
            out.append(f"{c.name}: {c.value} is above the maximum {c.maximum}")
    return out
