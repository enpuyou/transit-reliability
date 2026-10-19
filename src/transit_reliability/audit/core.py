"""Pure parts of the audit: a check spec, its verdict, and file-list reconciliation."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

# Runs one SQL statement and returns its single value (or None when there is no row/value).
Runner = Callable[[str], float | None]

# An empty GTFS-realtime FeedMessage is this long; a collector file of this size has no entities,
# so bronze (one row per entity) correctly holds no row for it.
EMPTY_FEED_BYTES = 15


@dataclass(frozen=True)
class CheckSpec:
    """What to measure (`sql` returns one number) and the rule the number must satisfy."""

    name: str
    sql: str
    explanation: str
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class CheckResult:
    name: str
    value: float | None
    rule: str
    passed: bool
    sql: str
    explanation: str
    error: str | None = None


def describe_rule(minimum: float | None, maximum: float | None) -> str:
    if minimum is not None and maximum is not None:
        return f"{minimum} <= value <= {maximum}"
    if minimum is not None:
        return f"value >= {minimum}"
    if maximum is not None:
        return f"value <= {maximum}"
    return "no rule"


def evaluate(spec: CheckSpec, value: float | None, error: str | None = None) -> CheckResult:
    """Apply the rule. A missing value fails: no data must never look healthy."""

    if value is None:
        passed = False
    else:
        passed = (spec.minimum is None or value >= spec.minimum) and (
            spec.maximum is None or value <= spec.maximum
        )
    return CheckResult(
        spec.name,
        value,
        describe_rule(spec.minimum, spec.maximum),
        passed,
        spec.sql,
        spec.explanation,
        error,
    )


def run_checks(specs: Iterable[CheckSpec], runner: Runner) -> list[CheckResult]:
    """Run every spec. A query that errors is a failed check, not a crash of the audit."""

    results: list[CheckResult] = []
    for spec in specs:
        try:
            results.append(evaluate(spec, runner(spec.sql)))
        except Exception as error:  # noqa: BLE001 - report any failure as a failed check
            results.append(evaluate(spec, None, error=str(error)[:300]))
    return results


def render(results: Sequence[CheckResult]) -> str:
    """A plain-text table: status, name, measured value, rule."""

    lines = []
    for r in results:
        shown = "no value" if r.value is None else f"{r.value:g}"
        lines.append(f"{'ok  ' if r.passed else 'FAIL'} {r.name} = {shown} (rule: {r.rule})")
    failed = sum(1 for r in results if not r.passed)
    lines.append(f"{len(results) - failed} passed, {failed} failed")
    return "\n".join(lines)


def missing_nonempty_files(
    raw: Mapping[str, int], in_bronze: Iterable[str], empty_bytes: int = EMPTY_FEED_BYTES
) -> list[str]:
    """Raw files (name -> size) absent from bronze that are NOT explained by being empty.

    Bronze holds one row per feed entity, so a feed with no entities leaves no trace; those are
    expected. Anything else missing means data was dropped between landing and bronze.
    """

    present = set(in_bronze)
    return sorted(name for name, size in raw.items() if name not in present and size > empty_bytes)


def epoch_gaps(epochs_ms: Sequence[int], max_gap_s: float) -> list[tuple[int, float]]:
    """(epoch_ms of the file after the gap, gap seconds) for gaps longer than `max_gap_s`."""

    ordered = sorted(epochs_ms)
    return [
        (b, (b - a) / 1000.0)
        for a, b in zip(ordered, ordered[1:], strict=False)
        if (b - a) / 1000.0 > max_gap_s
    ]
