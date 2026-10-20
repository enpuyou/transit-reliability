# 0016: Independent audit, and the stale next-stop pointer fix

Date: 2026-10-19
Status: accepted

## Context

The owner cannot review everything an agent writes, and checks written by the same agent share its
blind spots (the 2026-10-16 watermark bug passed every job status). A separate agent, given only the
project goals and read-only access, reviewed the code and ran its own SQL on prod.

## Findings (audit of 2026-10-19; see `docs/findings.md` for effects)

1. HIGH, fixed: a stale pointer on a trip's first fix capped every arrival (`arrivals/infer.py`).
2. Not fixed, documented: first and last timepoints of a trip are not measured (66 percent coverage).
3. Not fixed, documented: "early" is judged on arrival; partial days look like service failures;
   a trip with any cancellation flag is labelled canceled even if it ran; some prediction errors are
   a day off; a later feed change would not refresh `scheduled_stop_times` for dates already built.
4. Confirmed clean: bronze to silver reconciliation, keys, DST and after-midnight times, units, speeds.

## Decision

- Within a trip the pointer only moves forward, so a leading fix whose pointer is larger than a later
  fix's pointer is stale; `drop_stale_pointers` blanks it (its position still counts).
- Rebuilt prod `stop_visits` and the gold tables from 2026-10-13 on 2026-10-19 (one submitted run, then
  health checks passed). Verified in a sandbox first (wide intervals 7.68 -> 0.09 percent).
- The auditor's 28 SQL checks live in `src/transit_reliability/audit/`; run them with
  `docs/commands.md` ("Independent audit"). After the rebuild 24 pass; the 4 failures are the
  structural coverage items and the next-day errors above.
- Independent review of this kind should be repeated at each phase boundary.

## Found afterwards (2026-10-19, by the proof report on five random trips)

The feed's next-stop pointer sometimes runs AHEAD of the bus; the inference trusts it over the GPS
geometry, collapsing the arrival interval to the pointer's time. On 2026-10-17, 26,707 of 282,059
measured arrivals (9.5 percent) have a zero-width interval; example trip 833153111 stop 29: pointer
advanced at 01:04:38 UTC, bus 2,729 ft away, the stop's departure estimate says it arrived later.
Recorded as warn check `zero_width_arrival_interval_share`; not fixed (another prod rebuild; decide
first whether to trust the geometry, or widen the interval to include both).

## Still open

Wiring the checks into a results table and an hourly freshness check; a check for a scheduled job that
never starts (see `docs/commands.md`, 2026-10-19 traps); decisions on the unfixed metric definitions.
