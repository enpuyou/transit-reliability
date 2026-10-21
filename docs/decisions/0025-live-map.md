# 0025: A live map of buses, with each bus's trip and history (proposed)

Date: 2026-10-21
Status: accepted 2026-10-21; milestone 1 and part of 2 built locally, not yet deployed.

## Owner's answers (2026-10-21)

Public; a second page on the existing site; the popup should show the agency's prediction, scheduled
time, current location and our historical average at that stop; today's actuals are computed in the
browser from live positions (history and typical times are static exports of past days).

## Built so far

- `workers/relay`: serves `/vehicles` and `/alerts` only (a fixed list, so it is not an open proxy), adds
  the browser permission header, keeps each answer 15 seconds in Cloudflare's cache. Tested locally
  against the real feed: 536 buses, 52 KB.
- `site/gtfsrt.js`: a 100-line reader for the vehicle file, checked against the Python parser on the
  saved sample (`site/test/`). Hand-written to avoid a 100 KB library.
- `site/map.html`, `site/map.js`: dots by route, route filter, popup with route, last report time and
  speed. MapLibre is bundled in `site/vendor`; background tiles from OpenFreeMap.
- `site/map/routes.json` (from `scripts/make_map_data.py`): internal route id to route number.

## Not built yet

Deploy of the relay (needs the owner's go-ahead: new public endpoint), the real relay address in
`map.js`, the agency prediction / scheduled time / history popup (milestones 3 and 4), route lines.
Not seen in a browser yet: only the pieces were tested.

## What the owner asked for

A live map of buses: where they are right now, plus each bus's trip and its history.

## What we checked (2026-10-21)

- The agency's live feed (`vehiclepositions.pb`) is 52 KB with about 530 buses at midday. Each bus has its
  position, direction of travel, speed, route, trip, status (stopped or moving), the next stop number, and an
  occupancy field.
- A browser cannot read that feed directly: the server sends no permission header (CORS) for other websites.
  We need our own small relay.
- A free map background exists that works from a website with no account or key: OpenFreeMap (checked:
  style loads, allows browsers). Its terms and attribution wording must be read before launch (unverified).
- Our rule stands: the public site never queries the Databricks warehouse. Anything historical is
  exported ahead of time as static files.

## Design in two layers

**1. Live layer (seconds fresh), no Databricks.**
- A new tiny Cloudflare Worker (separate from the collector, so it cannot disturb collection) fetches the
  agency feed and passes the bytes through with the permission header, remembering the answer for 15 to 20
  seconds. It does not decode anything (decoding is the costly part of a free Worker's tiny time budget).
- The map page decodes the feed in the browser (a small open-source library, bundled with the site) and
  draws each bus as a dot coloured by route, with direction. Each dot moves as new positions arrive.
- Cost: free tier allows 100,000 Worker requests a day; one open map polling every 20 seconds is about 4,300.
  The 15 to 20 second memory means many viewers still cost the agency feed about three requests a minute.

**2. Trip and history layer (hours fresh), from Databricks exports.**
- Hourly, the existing publish job also writes static files: route lines and stops (simplified), today's and
  yesterday's trips with scheduled versus estimated times at each timepoint, and a history per trip over the
  last 14 days (share on time, typical lateness at each timepoint).
- Click a bus: the page looks up its trip in those files and shows the stops with scheduled and estimated
  times, how late it is so far, and how this same trip has run on previous days.
- The bus's own track today (its breadcrumbs) can start as "positions since you opened the page" and later
  become an export of the last hour of tracks (about 3 MB), if wanted.

**Overlays later:** detour alerts on affected routes, weather, bunching markers, "unusually slow here" markers
(from the ML plan, question 3).

## Honest limits

- The live layer shows where buses are, not how late they are: the live feed has no lateness. Lateness comes
  from our hourly exports (so up to an hour old) or from a second relay for the agency's prediction feed
  (1.3 MB, too heavy for a free Worker to decode; not planned).
- The trip history is our estimate with a range of about a minute, like everything on the site.
- This is the agency's public data under its terms: the attribution line must appear on the map.

## Milestones (each can be shown on its own)

1. Live buses on a map (relay Worker, map page, attribution). About a day.
2. Route lines and stops, click a bus to see its route and trip.
3. Today's trip so far: scheduled versus estimated at each timepoint (hourly export).
4. A trip's history over the last 14 days.
5. Overlays: detours, weather, bunching.

## Questions for the owner

1. Public or private? Public means anyone with the link, like the current site. (The data is the
   agency's public data; the relay is rate-limited by Cloudflare's free tier.)
2. Where should it live: a second page on the existing site (recommended: same address, same look)?
3. Is "positions plus lateness up to an hour old" acceptable for lateness, or must lateness be live too?
4. Scope of the first version: milestones 1 and 2, or 1 to 3?
