# 0027: Seattle Fire 911 dispatches as incident context

Date: 2026-10-22
Status: accepted; built and tested, deployed with the next prod deploy.

## Why

A collision or a fire on a bus's street can block a lane, slow buses or force a detour. Unlike events, incidents
are not known ahead, so they explain delays afterwards ("this stretch was slow; there was a collision here").

## What

- **Source:** City of Seattle open data, "Seattle Real Time Fire 911 Calls" (dataset kzjm-xkqj), updated every
  5 minutes, free, no key, readable directly from a browser. About 370 dispatches a day (2026-10-21/22), most of
  them medical; about 10 a day are motor vehicle incidents ("MVI"). Times are Seattle local time with no zone.
- **Fetch:** GitHub workflow `incidents.yml` every 2 hours asks for the last 30 hours, so late or missed runs
  lose nothing. Landed in `raw/incidents/dt=<date>/<epoch_ms>-incidents.json`.
- **Silver `incidents`:** one row per incident number, times turned into UTC, a category (traffic, fire,
  medical, other). Repeats inside each batch are dropped before the MERGE (lesson of the 2026-10-22 weather
  incident). **Gold `incident_routes`:** traffic and fire incidents with every route that has a stop within
  150 m. On 2026-10-22, 20 of 26 collisions and fires in 30 hours were that close to a bus stop.
- **Map:** with a route chosen, the page reads the last 3 hours of dispatches directly from the city and lists
  collisions and fires within 150 m of the route, with markers.
- **Checks (daily):** newest pull under 26 hours old; one row per incident.

## Limits

Fire department dispatches only (no police calls). A dispatch says something happened, not whether a road was
closed or for how long; most medical calls affect nothing. A slow bus near an incident shows association, not
cause.
