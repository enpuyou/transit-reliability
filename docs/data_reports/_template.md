# <Source name>

Status: **Observed | Planned | Unverified**
Profiled: <date or not yet>
Source owner: <agency or organization>
Format: <JSON, CSV, protobuf, API, etc.>
Source URL: <link>

## Executive summary

Explain the source in plain language. What real-world process does it represent? What
would a planner, PM, or stakeholder learn from it?

## What is in the source

| Dataset or endpoint | One record means | Cadence | Coverage |
|---|---|---|---|
| <name> | <meaning> | <cadence> | <known coverage> |

## Example record

Show one real decoded record or a clearly labeled representative example. Include the
capture date and identify any fields omitted for readability.

```text
<example>
```

### What this means

Translate the example into plain language. Explain identifiers, timestamps, geography,
status values, and any surprising fields.

## Field map

| Field | Meaning | Type or units | Useful output |
|---|---|---|---|
| `<field>` | <meaning> | <type or units> | <analysis or product use> |

## What we can do with this source alone

- <direct analysis or product capability>

## What requires joins or history

- <static reference data>
- <historical collection>
- <other source>

## Limitations and quality checks

- <missingness, ambiguity, bias, freshness, licensing, privacy, or causality caveat>

## Engineering plan

Describe raw retention, bronze parsing, silver cleaning, gold outputs, and tests.

## Product story

Explain the questions this source helps answer, who benefits, and what a presentation
could show. Separate observed facts from hypotheses and recommendations.

## Related reports

- [Data report index](README.md)
