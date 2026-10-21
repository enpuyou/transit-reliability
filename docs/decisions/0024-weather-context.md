# 0024: Weather as context for reliability (Phase 6, first context source)

Date: 2026-10-21
Status: accepted and built

## Decision

- Source: National Weather Service observations (`api.weather.gov`), US government data, no key, browser
  access allowed. Three stations: KSEA (Sea-Tac), KBFI (Boeing Field), KRNT (Renton), a reading about every
  5 minutes with temperature, wind, gusts, visibility, humidity, pressure, a conditions description and
  present-weather codes (rain, drizzle, showers, snow, fog or mist with light, moderate or heavy intensity).
- Not available: rain AMOUNTS (the precipitation fields were empty in all 400 observations checked at each
  of three stations on 2026-10-21). Wetness is read from the present-weather codes. So we can say whether it
  rained and how hard (light, moderate, heavy) but not how many millimetres.
- History: the service keeps about 7 days. Collection began 2026-10-13 21:43 UTC; the weather backfill covers
  2026-10-14 18:30 UTC onward (the first partial day is lost; not recoverable).
- Collection: Databricks blocks most outbound internet, so a GitHub workflow (`weather.yml`,
  `scripts/fetch_weather.py`) fetches the last 6 hours each hour and uploads the files to
  `raw/weather/dt=<date>/`. The Cloudflare dispatcher Worker starts it at :20 (decision 0020; GitHub's own
  schedule is a fallback). Overlap between runs is harmless; a missed run is recovered by the next.
- A quirk found while building: the service's "next page" link drops the time window and pages back
  through its whole retention, so the script filters every page by timestamp and stops once a page passes the
  requested start.
- Cleaning: `weather_observations` (silver, one row per station and time, deduplicated) parsed directly from
  the landed files (weather is re-fetchable, unlike the bus feeds, so no separate bronze); `weather_hourly`
  (one row per UTC hour, labelled rain when over a quarter of the hour's readings report precipitation, fog
  when over a quarter report fog, otherwise dry; the Seattle date and hour are stored for joins).
- Analysis: `gold.weather_impact` matches each trip to the weather in the Seattle hour it was scheduled to
  start (and each segment run to the hour it departed), and compares rider-rule shares and running time
  across classes within the same day type and time band. Counts are summed, never averaged. Trips from 4 am on
  only. This shows association, not cause; the website says so.
- Minimum evidence: a class is shown on the website only with at least 24 hours in that time band; below that
  the chart says how many rainy hours there have been and why it cannot yet compare.

## What the first week shows

168 hours: 151 dry, 15 foggy, 2 rainy (about 17 readings of light rain on Saturday 2026-10-17 and 10 am
that day at moderate). The week was almost dry, so rain versus dry cannot be judged yet. Weekday midday, dry
vs fog: 71.4 vs 70.5 percent on time, median running time 27 s vs 23 s faster than scheduled (5 foggy hours,
not enough to conclude). The comparison becomes meaningful as wet weather arrives; the pipeline keeps running.

## Checks

Hourly: newest observation at most 240 minutes old; at least two of three stations reported in the last 3
hours. Daily audit: no duplicate (station, time) keys, no hour without observations, and the impact table's
scored total equals an independent recount (EXISTS instead of a join). The row count was verified against an
independent pure-Python parse of the same files (5,976 = 2,004 + 1,978 + 1,994).

## Not done

Weather by route (stations are few and far from most routes), snow and wind effects (no cases yet),
temperature thresholds. Possible later: NOAA hourly climate data for older history; a model feature set is in
`docs/ml_plan.md`.
