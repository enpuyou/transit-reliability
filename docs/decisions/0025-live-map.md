# 0025: A live map of buses, with each bus's trip and history (proposed)

Date: 2026-10-21
Status: accepted 2026-10-21. Relay deployed 2026-10-21 at https://transit-relay.youenpu.workers.dev (verified: 200 with the permission header, second request served from cache, other paths 404). Popup built; page not yet published (needs a push).

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

## Popup (built 2026-10-21)

Clicking a bus loads the agency's prediction file through the relay (`/trips`, 1.1 MB, remembered 15 s),
finds that bus's trip in the browser, and shows the next three stops plus the next timetable stop that has
history: agency predicted time, timetable time (= predicted time minus the feed's delay), and how late buses
usually are there at that time of day (median, and the lateness 1 trip in 7 exceeds), from
`site/map/stop_history.json` (28-day window; only stops with a timetable time, intervals under 2 minutes
wide, at least 5 trips). "Today so far" actuals are not shown (the daily job runs at 4:45 am Pacific).

## Route lines and live updates (built 2026-10-21)

- `site/map/shapes.json` (421 KB, made by `scripts/make_map_data.py`): for each route direction, the
  shapes at least 20% of its trips use (331 lines, 19,000 points after simplifying to within 8 m). Drawn
  as thin lines under the buses; clicking a bus (or choosing a route) makes that route's lines bold.
- The page asks the relay every 10 seconds by itself (the relay still asks the agency at most every 15 s),
  and each bus glides from its old to its new position over 9 seconds, so movement looks continuous
  instead of jumping. An open popup follows its bus and refreshes its predictions each update.
- Tested with a script (stand-in browser objects, real relay and files): 331 lines, bus position
  moved start -> early -> halfway -> target as expected, popup followed and kept refreshing.

## Direction (built 2026-10-21)

- Each bus is an arrow pointing the way it is heading (a plain dot if unknown). The agency's feed carries a
  heading for only 22 of 536 buses (checked 2026-10-21), so for the rest `site/heading.js` reads it off
  the bus's route line, which runs in the direction of travel (`route|direction` picks the right line).
  Where the agency does report a heading, that wins.
- Checked against the 22 buses that do report one: 20 agree within 45 degrees live, 15 of 21 on the saved
  sample. The disagreements looked like buses at terminals, waiting, or out of service (unverified).
- Highlighting a route also draws white arrows along it showing the flow in each direction. The popup says
  "heading <compass>" and "Toward <last stop of the trip>" (from the agency's prediction file).

## Direction picker (built 2026-10-21)

Choosing a route enables a second dropdown listing its directions by destination ("toward Downtown
Seattle"), from `site/map/headsigns.json` (the sign text most trips of that direction show; one of 280
route directions has none and shows "direction N"). Picking a direction shows only that direction's
buses, highlights only its line, and zooms to it. Tested with the stand-in-browser script: route 1,
both directions 5 buses, direction 1 only 3 buses.

## Stops of the selected route (built 2026-10-21)

`site/map/route_stops.json` (292 KB, loaded the first time a route is selected): for each route direction
the stops of one representative trip per main pattern (9,132 stop entries over 280 route directions), with
a flag for timetable stops (timepoints). Every stop has a scheduled time (checked 2026-10-21: 0 of 1.13 million stop times lack one); only 19% are timepoints, where the schedule holds buses to the time and our history is kept. Selecting a route (dropdown, or clicking a bus) draws them as
rings along the line; choosing a direction narrows them; clicking a stop names it. Checked 2026-10-21:
every listed stop lies within 200 m of its route line. A branch's stops that no main pattern uses (under 20%
of trips) are not listed.

## Water-drop buses and stop arrivals (built 2026-10-21)

- Each bus with a known heading is a water-drop in its route colour whose pointed tip shows the way it is
  going (a plain disc when the heading is unknown). Checked by drawing the icon alone at four headings.
- Clicking a stop lists the next four predicted arrivals there (route, minutes away, minutes late), read from
  the agency's trip-update file, refreshing with every update. This is the answer to "can I catch the bus
  at this stop": the prediction uses the bus's position and delay, and the dots are a median 65 seconds old
  (measured 2026-10-21: about 41 s of that is before the agency publishes the file).

## Bus marker (2026-10-21)

Five styles were compared (round body with a pointer, disc with an arrow inside, disc with a beam, arrowhead,
smaller water-drop). The owner chose **a dot with a beam**: a disc in the route colour with a white outline
(radius 5 px zoomed out to 11 px zoomed in) and a faint wedge fanning out in front of it showing the heading,
like the Apple Maps location dot. A bus with no known heading is a plain dot. The other four were removed.
The research found nothing on how Transit, Moovit or Citymapper draw vehicles (unverified).

## Can the lag be compensated? (measured 2026-10-21, `scripts/lag_experiment.py`)

On 250 trips from 2026-10-20 (46,000 reports), the error between where a bus is drawn and where it really was
60 s after its report: leaving it at the last report is off by a median 823 ft (about 250 m), 90th percentile
2,224 ft (about 680 m). Moving it forward along the route at 75% of its recent speed cuts that to median 407 ft
(about 125 m) and 90th percentile 1,374 ft: about half. Full speed does worse than 75% (buses stop). It is not
built: showing an estimated position needs a clear label, and the estimate is wrong at stops and on detours.
Truth here is itself the next GPS report projected onto the scheduled route line.

## Stops and routes (2026-10-21)

All stops look the same (small white ring). While a route is chosen, every other route's line is hidden.

## Why a report is already old when the agency publishes it (measured 2026-10-20, 1.7 million reports)

Median 34 s old (middle half 28 to 41 s, 5th to 95th percentile 18 to 51 s). A bus sends a new fix about every
18 s (median) and the agency writes a new file about every 20 s, which explains roughly 10 to 20 s of
that age; the rest (about 15 to 25 s) is transmission and processing inside the agency's system, which we
cannot see (unverified cause). Our relay and polling add up to about 25 s more.

## Live traffic (TomTom), 2026-10-21

TomTom flow tiles (style `relative0`, 512 px) through the relay, which holds the key as a Worker secret and
caches each tile 90 s. Why not call TomTom from the browser: the key would be public and TomTom marks tiles
no-store, so every view would spend the allowance. Tiles are limited to the Seattle area and zoom 9 to 17.
Coverage test (80 random bus stops, TomTom's flow segment API): 80 had data (68 local connecting streets, 6
major roads, 3 secondary, 3 highways); at street zoom the raster tiles colour the whole street grid, at zoom 12
only highways. Truly residential streets were not tested (bus stops are on connecting roads). Unverified: the
terms for showing TomTom traffic over a non-TomTom map and for caching tiles; whether a free key can be
restricted to a website. Google's traffic cannot be used (its terms bar extracting content for other maps).

## Position estimate and accuracy check (2026-10-21)

Each bus is drawn where it probably is now: last report moved forward along its route line at 75% of its recent
speed, at most 120 s (`site/estimate.js`, tick box on the page, on by default). The line under the map compares
each estimate with the bus's real position at that moment, found by interpolating between its reports by the
GPS time stamp each carries, so late delivery does not matter. Assumption: the stamp is when the fix was taken,
not when the server received it (unverified; evidence: at first stops, where buses wait for the schedule, 10% of
departures were 29 s or more early and 5% 61 s or more, which would be rare if stamps ran 30 to 40 s late).
A stopwatch check at a stop is the only fully independent test. Another assumption: the viewer's computer clock
is right to a few seconds.

## Not built yet

- The history file is made by running `scripts/make_map_data.py` by hand; it should be refreshed daily
  (add a step to the daily silver job and copy it in the publish workflow; needs a prod deploy).
- Detour/weather overlays, a per-bus breadcrumb trail, 
- Not seen in a real browser: tested with a script that loads `map.js` with stand-in browser objects, the
  real relay and the real data files, and clicks a live bus.

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
