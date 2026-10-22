# 0026: Events from Ticketmaster, pulled once a day

Date: 2026-10-22
Status: accepted; built and tested locally, not yet deployed (needs the prod bundle deploy and a push).

## Why

Games, concerts and shows are known days ahead, so unlike weather or incidents they can warn before a delay
("Kraken game at Climate Pledge Arena ends about 9:45 pm; routes near Seattle Center are often slower") as well
as explain one afterwards. The owner chose events as the next context source (over Seattle Fire 911 dispatches,
which only explain delays after the fact).

## What

- **Source:** Ticketmaster Discovery API (free key, 5,000 calls a day; we use 1 to 5). Search: 30 miles around
  downtown Seattle, the next 21 days, about 360 events (2026-10-22). Ticketmaster gives no venue capacity, and only
  102 of 361 events had an end time; 338 had a start time.
- **Daily pull** (GitHub workflow `events.yml`, 09:17 UTC): `scripts/fetch_events.py` saves each result page as
  received, except the API key Ticketmaster copies into its own page links, which is removed before saving
  (checked: no saved file contains the key). Landed in `raw/events/dt=<date>/<epoch_ms>-p<page>.json`. A daily
  pull is enough: schedules change slowly and events are announced weeks ahead.
- **Silver `events`:** the newest version of each event (each pull lands the next 3 weeks again; an event can be
  moved or cancelled). **Gold `event_routes`:** each event with every route that has a stop within 400 m of the
  venue, and that stop's distance.
- **Large venues:** a small list of stadiums, arenas and big theatres with approximate seat counts
  (`events/parse.py`, unverified numbers), because big crowds are what can slow buses. 18 of 49 events in the next
  three days were at large venues (2026-10-22).
- **Map:** with a route chosen, "Events near route N" lists events in the next 36 hours whose venue has a stop of
  that route within 400 m (large venues first, marked ★), with venue markers on the map.
- **Checks (daily):** newest pull under 50 hours old; one row per event; one row per event and route.

## Not yet

- Measuring how much events actually slow buses (needs a few weeks of events and arrivals): compare segment
  running times near a venue in the hours before and after events with the same weekday and hour without one.
- Event end times are mostly missing; the "after" window will have to assume a typical length per kind of event.
