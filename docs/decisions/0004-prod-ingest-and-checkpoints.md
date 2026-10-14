# 0004: Run bronze ingest in prod with per-target checkpoints

Date: 2026-10-14
Status: accepted

## Context

Dev ingest was paused and had only been run against smoke fixtures. Real collection began
2026-10-13 about 21:43 UTC. The first real ingest run exposed two problems:

1. An unbounded first micro-batch over the trip-update backlog killed the Python worker
   (`Connection reset`). Vehicle positions, which are small, were fine.
2. Dev and prod wrote checkpoints to the same path (`checkpoints/bronze_<source>`). The
   first prod run resumed from dev's checkpoint, ingested only the 10 newest files, and
   reported `SUCCESS` in 83 s. It also consumed those files from dev's point of view.

## Decision

- Cap micro-batches with `cloudFiles.maxFilesPerTrigger` (trip updates 50, others 1000)
  and build parser output one file at a time.
- Checkpoint path is `<schema_prefix>_bronze_<source>`, so each target has its own.
- Run the 30-minute ingest in **prod** (`transit_bronze`), with dev paused. Recover by
  dropping the two partial prod tables and letting the fresh checkpoint backfill from the
  raw files, which were never touched.
- Set `workspace.root_path` for the prod target (required by `mode: production`).

## Alternatives considered

- Unpause dev and keep using it: rejected as the long-term path. Any dev deploy changes
  the code the schedule runs, and silver should not be built on `dev_*` names.
- Deploy prod immediately without a dev run: rejected; the first real run found a bug that
  smoke fixtures did not.

## Consequences

- Prod bronze holds all landed files from the start of collection. Dev bronze is stale and
  can be dropped.
- Any new stateful object (checkpoint, table, export path) must be namespaced by target.
- A job state of `SUCCESS` is not proof of correct ingest. Verify distinct `source_file`
  against landed files after every change (`docs/commands.md`, "Daily ingest check").
- Open: the first run had ~11 minutes with no output before the first table write; the
  cause (serverless startup, file listing, or backlog size) is not isolated.
