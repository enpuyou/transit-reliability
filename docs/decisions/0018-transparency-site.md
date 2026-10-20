# 0018: A public transparency site, built from aggregates, hosted outside Databricks

Date: 2026-10-20
Status: accepted (the owner asked for a comprehensive, externally hosted view of the whole pipeline)

## Context

The owner wants a dashboard that lets someone who knows nothing about the pipeline see what happens at
each step, how the data looks, and the monitoring and trends. Databricks dashboards would run on the
single 2X-Small warehouse (which failed to start on 2026-10-19) and need a workspace login; the
CLAUDE.md rule forbids querying the warehouse from a public path.

## Decision

- A job builds a `<prefix>_public` schema of aggregate tables (`public/queries.py`) and exports every
  table of that schema to one JSON file, `public/site.json` in the state volume (`public/build.py`).
  The hourly monitor job refreshes the live sections; the daily silver job also the heavy ones
  (daily counts, check trend, lineage, billing, distributions, masked samples).
- A GitHub workflow (`publish_site.yml`) copies that one file and publishes `site/` to GitHub Pages
  hourly. It fails when the file is missing, invalid, or older than 3 hours.
- The page (`site/`) reads only `data/site.json`; it draws charts itself (no external scripts) and
  inserts data only as text, never as HTML (alert headlines are external text).
- The website never queries Databricks. The warehouse is never reachable from the public page.
- Privacy by construction, enforced by tests (`tests/test_public.py`): no query selects a raw vehicle
  id or exact coordinates; sample rows hash the id and round coordinates to two decimals (about 1 km).
  Aggregates and a few sample rows only. KCM's required attribution legend is in the data and footer.
- Lineage, job history and billing come from Databricks system tables (readable on Free Edition,
  verified 2026-10-19), so the "how tables depend on each other" graph is Databricks' own record.
- The page states its limits plainly (arrival times are estimates; about a third of timepoints are
  unmeasured; early is judged on arrival; the zero-width interval issue; partial days) because a
  transparency page that hides them would not be transparent.

## Consequences

- One data layer feeds the page; a Databricks dashboard can be added later on the same tables.
- Pipeline data is public (the repository already is). The token in GitHub secrets has the owner's full
  permissions (Free Edition has no account-level service principals; unverified whether workspace-level
  ones can be scoped), so the protection is that the workflow only runs `fs cp` of one file.
- The page is as fresh as the last monitor run (hourly) and the workflow (hourly at :35).
- Metro publishes its own dashboards (decision 0008, gate G3): this page explains a pipeline and
  independently measures with intervals; it does not claim to be the only source.
