# <Source name>

| | |
|---|---|
| Status | **Planned** / Collecting / Observed / Unverified |
| Phase | <design-doc phase number> |
| Owner | <agency or organization> |
| Format | <JSON, CSV, protobuf, API, etc.> |
| Cadence | <how often it changes or is polled> |
| Profiled | <date or not yet> |
| Target tables | <bronze/silver/context tables this feeds> |

## Executive summary

What real-world process does the source represent, and what would a planner learn from
it? Keep to a short paragraph.

## Example record

One real decoded record, or a clearly labeled representative one, with capture date.

```text
<example>
```

Explain identifiers, timestamps, geography, and surprising fields in a sentence or two.

## What we can do with this source

- <analysis or product capability from this source alone>
- Needs joins or history: <other sources or collected snapshots required>

## Engineering plan and quality checks

Raw retention, bronze/silver/gold outputs, keys and dedupe, known caveats (freshness,
missingness, bias, licensing, privacy, causality), and tests.

## Product story

Questions this source helps answer, who benefits, and what to avoid claiming.

## Related reports

- [Data report index](README.md)

---

Optional sections once real data exists, in this order: **Field map** (field, meaning,
units, useful output), **Observed behavior** (measured counts and change rates, with
date), **What requires joins or history**.
