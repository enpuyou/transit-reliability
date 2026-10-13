# 0003: Poll vehicle positions every 20 seconds

Date: 2026-10-13
Status: accepted, with a CPU-limit watch item

## Context

KCM regenerates the vehicle/trip feeds about every 21 seconds and buses report about
every 16 seconds (`docs/collection_pipeline.md`, section 1b). One pull per minute captured
about one in three feed versions. Cloudflare cron triggers fire at most once per minute.

## Decision

Keep the one-minute cron. Inside each run, fetch vehicle positions three times, 20 seconds
apart, by waiting between fetches. Fetch trip updates once per run and alerts every fifth
minute. Name files by actual fetch time (epoch ms). Skip an upload when the ETag equals the
previous fetch in the same run.

## Evidence (2026-10-13)

- An HTTP-triggered throwaway Worker confirmed that `await sleep(20000)` between fetches
  works on the Free plan (3 fetches at 0.4 s, 20.9 s, 41.3 s; 4 ms CPU).
- On the live collector, 11 consecutive cron runs after the change: all `outcome: ok`,
  wall time about 43 s, CPU 6 to 10 ms (values: 10, 7, 8, 6, 6, 7, 6, 7, 6, 9, 6).
- Landed vehicle files now arrive about every 21 s, each a distinct feed version, except
  occasional duplicates across run boundaries (one seen in 40 files). Dedupe on
  `feed_header_ts` in bronze/silver.
- Storage: vehicle positions add about 3 x 60 KB per minute (about 260 MB per day); trip
  updates unchanged.

## Watch item: CPU limit

The Free plan allows 10 ms CPU per cron run. Before this change the run used 4 to 5 ms; now
6 to 10 ms. One run reached 10 ms. If a run exceeds the limit Cloudflare terminates it and
that minute is lost. Mitigations, in order of preference:

1. Move to the paid Workers plan (removes the 10 ms cap).
2. Split into two Workers, one for vehicle positions and one for trip updates and alerts,
   so each run gets its own budget. The code supports this (`SOURCE_SET` = `vehicles` or
   `trips`). It was tried on 2026-10-13 and NOT adopted: a newly created second Worker's
   cron never fired (also true of a throwaway Worker) after more than 5 minutes, which
   produced a 3-minute gap in trip updates (23:30 to 23:32) until the change was rolled
   back. Do not repeat that deployment without a way to confirm the new cron fires before
   removing the sources from the existing Worker.
3. Reduce CPU: stream the trip-update body straight into the upload instead of buffering.

Check CPU with the command in `docs/commands.md` after any Worker change.

## Consequences

- Arrival inference (Phase 5) has about 3x more position samples per bus.
- Raw file count for vehicle positions triples (about 4,300 per day).
