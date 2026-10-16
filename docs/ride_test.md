# Ride test: validating inferred stop times (gate G1)

The inferred arrival and departure times in `stop_visits` are consistent with two other signals
(the feed's stop pointer and when Metro drops a stop's prediction), but that is not accuracy. The
only real check is a trip where you know the true times. Target from the design doc: **median
absolute error under 60 seconds**, and the true time inside our interval most of the time.

## What you need
- A phone with a clock that shows seconds (set to automatic network time).
- A bus ride of at least 6 stops on a weekday, off-peak is easiest. Any route works; mix of a
  frequent route and a less frequent one is better.
- The vehicle number shown on the bus (the number painted by the front door or on the display).

## During the ride
Write down, at each stop, the clock time when the bus **stops and the doors open** (that is the
arrival we estimate) and, if you can, when the doors **close or the bus starts moving** (the
departure). Note the date, the route, and the vehicle number. Record at least 6 stops; 10 or more
is better. A notes app or a spreadsheet with one row per stop is enough:

```text
date, route, vehicle, stop_name_or_id, arrival_hh:mm:ss, departure_hh:mm:ss
```

Record local (Pacific) time. Do it on your own bus only; do not interfere with the driver.

## After the ride: find the trip
The vehicle number is the feed's `vehicle_id`. Find the trip it ran around your ride time:

```sql
SELECT DISTINCT trip_id, route_id FROM workspace.transit_silver.vehicle_positions
WHERE vehicle_id = '<vehicle>' AND service_date = DATE'<date>'
  AND vehicle_ts BETWEEN timestamp'<start UTC>' AND timestamp'<end UTC>';
```

(Convert Pacific to UTC: add 7 hours in summer time, 8 in winter.) Then read that trip's visits:

```sql
SELECT stop_sequence, stop_id,
       from_utc_timestamp(arr_est, 'America/Los_Angeles') AS arr_pacific,
       from_utc_timestamp(arr_lo, 'America/Los_Angeles') AS arr_lo_pacific,
       from_utc_timestamp(arr_hi, 'America/Los_Angeles') AS arr_hi_pacific, arr_method,
       from_utc_timestamp(dep_est, 'America/Los_Angeles') AS dep_pacific
FROM workspace.transit_silver.stop_visits
WHERE service_date = DATE'<date>' AND trip_id = '<trip>' ORDER BY stop_sequence;
```

## How to score it
For each stop you recorded: `error = our estimate minus your time` (seconds), and whether your
time fell between `arr_lo` and `arr_hi`. Report the median absolute error, the 90th percentile of
absolute error, the share inside the interval, and the number of stops with no estimate. Write the
results and the conditions in the table below.

| Date | Route | Vehicle | Stops | Median abs error (s) | p90 abs error (s) | Inside interval | No estimate |
|---|---|---|---|---|---|---|---|
| (not done yet) | | | | | | | |

## How to read the result
- Median absolute error at or under 60 s and most true times inside the interval: the method is
  usable for the metrics in the design doc; record the numbers in `docs/decisions/0011`.
- A consistent bias (for example always 20 s late): adjust `ARRIVE_TOL_FT` or `DEPART_TOL_FT`
  from this evidence, then rerun the tests and the stop-visits build.
- Large errors at particular kinds of stops (terminals, loops, stops after a gap): note which and
  report coverage honestly, and restrict later analyses to the routes and stops that pass.
