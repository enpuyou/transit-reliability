/* Live bus map. Every POLL_MS the browser asks the relay Worker for the agency's vehicle file, decodes it
   (gtfsrt.js) and glides each bus dot to its new position. Route lines come from map/shapes.json. An open
   popup follows its bus and refreshes by itself. No Databricks involved: the page only talks to the relay,
   the map tiles and files on this site. */
(function () {
  "use strict";
  // Where the relay Worker lives. Override for local testing with  map.html?relay=http://localhost:8787
  var RELAY = new URLSearchParams(location.search).get("relay") || "https://transit-relay.youenpu.workers.dev";
  var POLL_MS = 10000, GLIDE_MS = 9000, FRAME_MS = 100, SNAP_DEG = 0.01, STALE_S = 120, STYLE = "https://tiles.openfreemap.org/styles/liberty";

  var stateEl = document.getElementById("state"), countEl = document.getElementById("count"), pick = document.getElementById("routepick"), dirPick = document.getElementById("dirpick");
  var routes = {}, last = null, timer = null, filter = "", dirFilter = "", selected = "", selectedDir = "", shapes = null, heads = {}, tracked = null, routeStops = null, stopsAsked = false;
  var pos = {}, animating = false, lastFrame = 0;
  var estimateOn = document.getElementById("estimate");
  var checks = { est: [], raw: [] }, MAX_CHECKS = 800;
  var trafficOn = document.getElementById("traffic");

  function setState(cls, text) { stateEl.className = "status " + cls; stateEl.textContent = text; }
  function colour(routeId) {
    var h = 0, s = String(routeId || "");
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return "hsl(" + h + ",70%,42%)";
  }
  function esc(t) { return String(t).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
  function name(routeId) { return routes[routeId] || routeId || "unknown"; }
  function seattle(unix) {
    return new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit", second: "2-digit" }).format(new Date(unix * 1000));
  }
  function ago(seconds) { return seconds < 90 ? Math.round(seconds) + " s" : Math.round(seconds / 60) + " min"; }


  function headHtml(p) {
    var bits = ["<b>Route " + esc(p.label) + "</b>"];
    if (Number(p.offroute) === 1) bits.push('<span class="m"><b>Not on its route right now</b> (more than 150 m from the route line): it may be on its way to join its trip partway, on a detour, or not in service yet. Shown where it reported, with no estimate.</span>');
    if (p.ts) {
      var age = Math.max(0, Date.now() / 1000 - Number(p.ts));
      bits.push('<span class="m">position reported ' + seattle(Number(p.ts)) + " (" + ago(age) + " ago)" +
        (Number(p.est) === 1 ? "; the dot is an estimate of where it is now: moved forward along its route from that report using its recent speed and the timetable's pace (the hollow circle is the report itself)" : (age > 45 ? "; the bus has likely moved on since, a bus can cover about 100 m in 10 s" : "")) + "</span>");
    }
    if (p.heading != null && Number(p.heading) >= 0) bits.push("heading " + Heading.compass(Number(p.heading)));
    if (p.speed != null && p.speed !== "null") bits.push("speed " + Math.round(Number(p.speed) * 2.237) + " mph");
    return bits.join("<br>");
  }

  /* ---------- what the clicked bus is predicted to do, and what it usually does ---------- */
  var tripsCache = null, statics = null;
  function getTrips() {
    if (tripsCache && Date.now() - tripsCache.at < 15000) return Promise.resolve(tripsCache.buf);
    return fetch(RELAY + "/trips").then(function (r) {
      if (!r.ok) throw new Error("relay answered " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) { tripsCache = { at: Date.now(), buf: new Uint8Array(buf) }; return tripsCache.buf; });
  }
  function getStatics() {
    if (!statics) statics = Promise.all(["map/stops.json", "data/stop_history.json"].map(function (u) {
      return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }));
    return statics;
  }
  function pacific(unix) {
    var parts = {};
    new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", weekday: "short", hour: "numeric", hour12: false }).formatToParts(new Date(unix * 1000))
      .forEach(function (x) { parts[x.type] = x.value; });
    return { day: parts.weekday, hour: Number(parts.hour) % 24 };
  }
  function bucket(unix) { /* same time bands and day types as the daily numbers (scripts/make_map_data.py) */
    var t = pacific(unix), h = t.hour;
    var d = t.day === "Sun" ? "sun" : t.day === "Sat" ? "sat" : "wk";
    var b = h >= 6 && h <= 8 ? "am" : h >= 9 && h <= 14 ? "mid" : h >= 15 && h <= 18 ? "pm" : h >= 19 && h <= 23 ? "eve" : "night";
    var nice = { am: "morning rush", mid: "midday", pm: "afternoon rush", eve: "evening", night: "night" };
    var days = { wk: "weekdays", sat: "Saturdays", sun: "Sundays" };
    return { key: d + "." + b, text: days[d] + ", " + nice[b] };
  }
  function minutes(sec) { var m = Math.round(Math.abs(sec) / 60); return m + " min"; }
  function lateText(sec) { return Math.abs(sec) < 60 ? "on time" : minutes(sec) + (sec > 0 ? " late" : " early"); }
  function hhmm(unix) { return new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit" }).format(new Date(unix * 1000)); }

  function loadPredictions(p, el, quiet) {
    Promise.all([getTrips(), getStatics(), ensureTripHistory(p.route)]).then(function (res) {
      var trip = GtfsRt.findTripUpdate(res[0], p.trip), stops = res[1][0] || {}, hist = (res[1][1] || {}).h || {};
      if (!trip) { if (!quiet || !el.querySelector("b")) el.textContent = "The agency has no prediction for this trip right now."; return; }
      if (trip.canceled) { el.innerHTML = "<b>The agency has marked this trip canceled.</b>"; return; }
      // the agency can mark stops of a trip as skipped (for example when a replacement bus joins the trip partway)
      var ahead = trip.updates.filter(function (u) { return u.stopSeq >= Number(p.stopSeq); });
      var skipped = ahead.filter(function (u) { return u.skipped; });
      var skipNote = "";
      if (skipped.length) {
        var firstServed = ahead.filter(function (u) { return !u.skipped && u.arrTime; })[0];
        skipNote = "<b>Skips " + skipped.length + " stop" + (skipped.length > 1 ? "s" : "") + "</b> (" + esc(stops[skipped[0].stopId] || "stop " + skipped[0].stopId) +
          (skipped.length > 1 ? " to " + esc(stops[skipped[skipped.length - 1].stopId] || "stop " + skipped[skipped.length - 1].stopId) : "") + ")" +
          (firstServed ? "; first stop it will serve: " + esc(stops[firstServed.stopId] || "stop " + firstServed.stopId) + " at about " + hhmm(firstServed.arrTime) : "") + "." + "<hr style='border:0;border-top:1px solid #dcdfe6;margin:6px 0'>";
      }
      var next = ahead.filter(function (u) { return u.arrTime && !u.skipped; });
      var keyOf = function (u) { return p.route + "|" + p.dir + "|" + u.stopId; };
      var shown = next.slice(0, 3);
      var tp = next.filter(function (u) { return hist[keyOf(u)]; })[0];
      if (tp && shown.indexOf(tp) < 0) shown.push(tp);
      if (!shown.length) { el.innerHTML = skipNote || "No upcoming stops in the agency's predictions."; return; }
      var lastStop = trip.updates.length ? trip.updates[trip.updates.length - 1].stopId : null;
      var rows = shown.map(function (u) {
        var sched = u.arrDelay != null ? u.arrTime - u.arrDelay : null, b = sched ? bucket(sched) : null;
        var h = b && hist[keyOf(u)] && hist[keyOf(u)][b.key];
        var line = "<b>" + esc(stops[u.stopId] || "stop " + u.stopId) + "</b><br>" +
          "Agency predicts <b>" + hhmm(u.arrTime) + "</b>" + (sched ? ", timetable " + hhmm(sched) + " (" + lateText(u.arrDelay) + ")" : "");
        var mine = tripUsually(p.route, p.trip, u.stopId, sched);
        if (mine) line += '<br><span class="m">This trip usually gets here at <b>' + hhmm(mine.at) + "</b> (" + lateText(mine.late) + "; on 1 day in 7 " + minutes(mine.p85) + (mine.p85 > 0 ? " late or more" : " early or less") + "; " + mine.days + " past days).</span>";
        else if (h) line += '<br><span class="m">Usually ' + lateText(h[0]) + " here on " + b.text + "; 1 trip in 7 is " + minutes(h[1]) + (h[1] > 0 ? " late or more" : " early or less") + " (" + h[2] + " trips).</span>";
        return line;
      });
      var age = trip.feedTimestamp ? Date.now() / 1000 - trip.feedTimestamp : 0;
      el.innerHTML = (lastStop && stops[lastStop] ? "<b>Toward " + esc(stops[lastStop]) + "</b><hr style='border:0;border-top:1px solid #dcdfe6;margin:6px 0'>" : "") + skipNote + rows.join("<hr style='border:0;border-top:1px solid #dcdfe6;margin:6px 0'>") +
        '<div class="m" style="margin-top:6px">Predictions as of ' + hhmm(trip.feedTimestamp) + (age > 600 ? " — OUT OF DATE, ignore them" : "") +
        ". ‘This trip usually’ comes from this exact trip on past days; ‘usually … here on’ (when there is no trip history) is every trip of the route at that time of day, timetable stops only. Past days only (" + ((res[1][1] || {}).first_day || "?") + " to " + ((res[1][1] || {}).last_day || "?") + "); our arrival times are estimates from GPS.</div>";
    }).catch(function (err) { if (!quiet) el.textContent = "Couldn't load predictions (" + err.message + ")."; });
  }

  // ?base=none draws no background map (used for automated screenshots, where the tile server is not reachable)
  var BLANK = { version: 8, glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf", sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#e9ecef" } }] };
  var map = new maplibregl.Map({ container: "livemap", style: new URLSearchParams(location.search).get("base") === "none" ? BLANK : STYLE, center: [-122.33, 47.6], zoom: 10.5, attributionControl: { compact: true } });
  window.transitMap = map; // handy for debugging in the browser console
  window.transitBuses = pos;
  map.on("error", function (e) {
    console.log("map error: " + (e && e.error && e.error.message ? e.error.message : e));
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

  /* ---------- bus positions: every bus glides from where it was drawn to where it now is ---------- */
  function propsOf(v, heading, est, offroute) {
    return { id: v.id || "", route: v.routeId || "", color: colour(v.routeId), label: name(v.routeId), speed: v.speed,
      stopSeq: v.stopSeq, ts: v.ts, trip: v.tripId || "", dir: v.directionId, heading: heading == null ? -1 : heading, est: est ? 1 : 0, offroute: offroute ? 1 : 0 };
  }
  /* The agency's own heading when the bus reports one (few do); otherwise read it off the route line. */
  function headingOf(v) {
    if (v.bearing != null) return v.bearing;
    if (!shapes) return -1;
    return Heading.bearingOnLine(shapes[v.routeId + "|" + v.directionId], v.lon, v.lat, 150);
  }
  function receive(feed) {
    var now = performance.now(), seen = {};
    feed.vehicles.forEach(function (v) {
      if (v.lat == null || v.lon == null || !v.id) return;
      seen[v.id] = true;
      var cur = pos[v.id];
      if (!cur) { cur = pos[v.id] = { lon: v.lon, lat: v.lat, fx: v.lon, fy: v.lat, tx: v.lon, ty: v.lat, t0: now, v: v, heading: headingOf(v), hist: [], preds: [], est: false, vel: null }; track(cur, v, now); return; }
      var snap = Math.abs(cur.lon - v.lon) > SNAP_DEG || Math.abs(cur.lat - v.lat) > SNAP_DEG;
      cur.fx = snap ? v.lon : cur.lon; cur.fy = snap ? v.lat : cur.lat;
      cur.tx = v.lon; cur.ty = v.lat; cur.t0 = now; cur.v = v; cur.heading = headingOf(v);
      track(cur, v, now);
    });
    Object.keys(pos).forEach(function (id) { if (!seen[id]) delete pos[id]; });
    startAnimation();
  }
  /* ---------- estimating where a bus is NOW (the report is about a minute old) ---------- */
  function wallSeconds() { return Date.now() / 1000; }
  function lineFor(v) {
    var lines = shapes && shapes[v.routeId + "|" + v.directionId], best = null, bestOff = Infinity;
    (lines || []).forEach(function (line) {
      var p = Estimate.project(line, v.lon, v.lat);
      if (p.off < bestOff) { bestOff = p.off; best = { line: line, dist: p.dist, off: p.off }; }
    });
    return best && best.off <= 150 ? best : null;
  }
  function estDist(b, nowSec) { return Estimate.mixed(b.rep, b.vel, nowSec, b.len, b.tt); }

  /* ---------- each route's timetable and "previous trip of this bus" (site/map/trips/<route>.json) ---------- */
  var routeTrips = {};
  function ensureRouteTrips(routeId) {
    if (!routeId || routeTrips[routeId]) return Promise.resolve(routeTrips[routeId] || null);
    routeTrips[routeId] = fetch("map/trips/" + routeId + ".json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { routeTrips[routeId] = j; return j; }).catch(function () { routeTrips[routeId] = null; return null; });
    return routeTrips[routeId];
  }
  // how late each exact trip usually reached each stop over past days (data/trip_history/<route>.json, built daily)
  var tripHistory = {};
  function ensureTripHistory(routeId) {
    if (!routeId) return Promise.resolve(null);
    if (tripHistory[routeId] !== undefined) return Promise.resolve(tripHistory[routeId]).then(function (x) { return x && x.then ? null : x; });
    tripHistory[routeId] = fetch("data/trip_history/" + routeId + ".json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { tripHistory[routeId] = j; return j; }).catch(function () { tripHistory[routeId] = null; return null; });
    return tripHistory[routeId];
  }
  function tripUsually(routeId, tripId, stopId, schedUnix) {
    var h = tripHistory[routeId], x = h && !h.then && h.t[tripId] && h.t[tripId][stopId];
    if (!x || schedUnix == null) return null;
    return { at: schedUnix + x[0], late: x[0], p85: x[1], days: x[2], from: h.first_day, to: h.last_day };
  }
  function loadedTrips(routeId) { var r = routeTrips[routeId]; return r && !r.then ? r : null; }
  /* Timetable times count seconds from "noon minus 12 hours" of the service day in Seattle (the GTFS rule). */
  function serviceBase(yyyymmdd) {
    var y = Number(yyyymmdd.slice(0, 4)), m = Number(yyyymmdd.slice(4, 6)), d = Number(yyyymmdd.slice(6, 8));
    var hour = Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", hourCycle: "h23" }).format(new Date(Date.UTC(y, m - 1, d, 20))));
    return Date.UTC(y, m - 1, d, 12 + (20 - hour)) / 1000 - 12 * 3600;
  }
  function todayPacific() {
    var p = {};
    new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date()).forEach(function (x) { p[x.type] = x.value; });
    return p.year + p.month + p.day;
  }
  /* The bus's trip timetable laid along its route line, for the "timetable pace" half of the estimate. */
  function timetableFor(b, v) {
    var data = loadedTrips(v.routeId), trip = data && data.trips[v.tripId];
    if (!trip || !b.line) return null;
    var base = serviceBase(v.startDate || todayPacific()), stops = [];
    trip[5].split(";").forEach(function (part) {
      var bits = part.split(":"), at = data.stops[bits[0]];
      if (!at) return;
      var p = Estimate.project(b.line, at[0], at[1]);
      if (p.off <= 120) stops.push({ dist: p.dist, time: base + Number(bits[1]) });
    });
    return Estimate.timetable(stops);
  }
  /* Called for every bus on every update. When the bus has a NEW report: (1) check the estimates we drew earlier
     against where the bus really was at those moments (the report carries the time its GPS fix was taken, so a late
     delivery does not matter), (2) remember the report, (3) work out its speed. */
  function track(b, v, now) {
    if (!shapes || !v.ts) return;
    var fresh = !b.rep || v.ts !== b.rep.ts;
    if (fresh) {
      var hit = lineFor(v);
      if (!hit) { b.est = false; b.rep = null; b.hist = []; b.preds = []; b.offRoute = !!(shapes[v.routeId + "|" + v.directionId]); return; }
      b.offRoute = false;
      if (b.rep && b.line !== hit.line) { b.hist = []; b.preds = []; }
      var prev = b.hist.length ? b.hist[b.hist.length - 1] : null;
      b.line = hit.line; b.len = Estimate.length(hit.line);
      if (prev && v.ts > prev.ts && v.ts - prev.ts <= 60) {
        b.preds.forEach(function (q) {
          if (q.at < prev.ts || q.at > v.ts) return;
          var truth = prev.dist + (hit.dist - prev.dist) * (q.at - prev.ts) / (v.ts - prev.ts);
          checks.est.push(Math.abs(q.est - truth)); checks.raw.push(Math.abs(q.raw - truth));
        });
        if (checks.est.length > MAX_CHECKS) { checks.est.splice(0, checks.est.length - MAX_CHECKS); checks.raw.splice(0, checks.raw.length - MAX_CHECKS); }
      }
      b.preds = b.preds.filter(function (q) { return q.at >= v.ts; });
      b.rep = { ts: v.ts, dist: hit.dist };
      var ttKey = v.tripId + "|" + v.routeId + "|" + v.directionId + "|" + (loadedTrips(v.routeId) ? 1 : 0);
      if (b.ttKey !== ttKey || b.ttLine !== b.line) { b.tt = timetableFor(b, v); b.ttKey = ttKey; b.ttLine = b.line; }
      b.hist.push(b.rep); if (b.hist.length > 5) b.hist.shift();
      b.vel = Estimate.speed(b.hist);
    }
    b.est = b.vel != null && !!b.rep;
    if (b.est) { var w = wallSeconds(); b.preds.push({ at: w, est: b.shown != null ? b.shown : estDist(b, w), raw: b.rep.dist }); if (b.preds.length > 14) b.preds.shift(); }
  }
  function pct(list, p) { var a = list.slice().sort(function (x, y) { return x - y; }); return a[Math.min(a.length - 1, Math.floor(p * (a.length - 1)))]; }
  function metres(m) { return Math.round(m) + " m (" + Math.round(m * 3.281) + " ft)"; }
  function showChecks() {
    var el = document.getElementById("check");
    if (!el) return;
    var n = checks.est.length;
    if (n < 30) { el.textContent = "Accuracy check: collecting (needs about 3 minutes; " + n + " comparisons so far). Each estimate is compared with where the bus really was, once its later reports arrive."; return; }
    el.textContent = "Accuracy check, last " + n + " comparisons in this tab. Estimate off by a typical " + metres(pct(checks.est, 0.5)) + " (9 in 10 within " + metres(pct(checks.est, 0.9)) +
      "). Showing just the last report would be off by a typical " + metres(pct(checks.raw, 0.5)) + " (9 in 10 within " + metres(pct(checks.raw, 0.9)) + ").";
  }

  function step(now) {
    var moving = false, nowSec = wallSeconds(), compensate = !estimateOn || estimateOn.checked;
    Object.keys(pos).forEach(function (id) {
      var b = pos[id], t = Math.min(1, (now - b.t0) / GLIDE_MS);
      if (compensate && b.est && filter) {
        // keep moving the bus along its route at 75% of its recent speed since the report, but never backward:
        // when a new report says the bus was slower than guessed, the drawn bus slows down until the estimate catches up
        if (b.shownLine !== b.line) { b.shown = null; b.shownLine = b.line; }
        var dt = b.shownT == null ? 0 : (now - b.shownT) / 1000;
        b.shown = Estimate.advance(b.shown, estDist(b, nowSec), dt, b.vel); b.shownT = now;
        var e = Estimate.pointAt(b.line, b.shown);
        b.lon = e[0]; b.lat = e[1]; b.beam = e[2]; b.drawnEst = true;
        moving = true;
      } else {
        b.shown = null; b.shownT = null;
        b.lon = b.fx + (b.tx - b.fx) * t; b.lat = b.fy + (b.ty - b.fy) * t; b.drawnEst = false;
        if (t < 1) moving = true;
      }
    });
    return moving;
  }
  function render() {
    var feats = [], running = Object.keys(pos).length;
    Object.keys(pos).forEach(function (id) {
      var b = pos[id];
      if (!filter) return; // with every route selected there are too many buses to read: choose a route
      if (b.v.routeId !== filter) return;
      if (dirFilter && String(b.v.directionId) !== dirFilter) return;
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [b.lon, b.lat] }, properties: propsOf(b.v, b.drawnEst ? b.beam : b.heading, b.drawnEst, b.offRoute) });
    });
    var src = map.getSource("buses");
    if (src) src.setData({ type: "FeatureCollection", features: feats });
    // what we know next to what we guess: where each estimated bus really last reported (hollow circle, with how
    // long ago) and the movement we estimated since then (dashed line along the route)
    var ghosts = [], nowSec = wallSeconds();
    if (filter) Object.keys(pos).forEach(function (id) {
      var b = pos[id];
      if (!b.drawnEst || !b.rep || b.v.routeId !== filter || (dirFilter && String(b.v.directionId) !== dirFilter)) return;
      var at = Estimate.pointAt(b.line, b.rep.dist), age = Math.max(0, Math.round(nowSec - b.rep.ts)), color = colour(b.v.routeId);
      ghosts.push({ type: "Feature", geometry: { type: "Point", coordinates: [at[0], at[1]] }, properties: { color: color, label: age + " s ago" } });
      if (b.shown - b.rep.dist > 15) ghosts.push({ type: "Feature", geometry: { type: "LineString", coordinates: Estimate.slice(b.line, b.rep.dist, b.shown) }, properties: { color: color } });
    });
    var gsrc = map.getSource("bus-ghosts");
    if (gsrc) gsrc.setData({ type: "FeatureCollection", features: ghosts });
    countEl.textContent = filter
      ? feats.length + " buses on this route. " + (!estimateOn || estimateOn.checked ? "Each is placed where it probably is now (its last report is usually about a minute old)." : "Each is shown where it last reported, usually about a minute ago.")
      : "Choose a route to see its buses (" + running + " buses running right now).";
    if (tracked && pos[tracked.id]) tracked.popup.setLngLat([pos[tracked.id].lon, pos[tracked.id].lat]);
  }
  function frame(now) {
    if (now - lastFrame >= FRAME_MS) { lastFrame = now; var moving = step(now); render(); if (!moving) { animating = false; return; } }
    requestAnimationFrame(frame);
  }
  function startAnimation() { if (!animating) { animating = true; requestAnimationFrame(frame); } }

  /* ---------- route lines ---------- */
  function lineFeatures() {
    var feats = [];
    Object.keys(shapes || {}).forEach(function (key) {
      var route = key.split("|")[0], dir = key.split("|")[1];
      shapes[key].forEach(function (line) {
        feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: line }, properties: { route: route, dir: dir, color: colour(route) } });
      });
    });
    return { type: "FeatureCollection", features: feats };
  }
  /* ---------- the stops of the selected route (file is fetched the first time a route is selected) ---------- */
  function stopFeatures() {
    var feats = [], seen = {};
    if (!selected || !routeStops) return { type: "FeatureCollection", features: feats };
    Object.keys(routeStops).forEach(function (key) {
      var parts = key.split("|");
      if (parts[0] !== selected || (selectedDir && parts[1] !== selectedDir)) return;
      routeStops[key].forEach(function (st) {
        if (seen[st[0]]) return;
        seen[st[0]] = true;
        feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [st[1], st[2]] },
          properties: { id: st[0], tp: st[3], color: colour(selected) } });
      });
    });
    return { type: "FeatureCollection", features: feats };
  }
  function showStops() {
    var src = map.getSource("route-stops");
    if (src) src.setData(stopFeatures());
  }
  function ensureStops() {
    if (stopsAsked || !selected) return;
    stopsAsked = true;
    fetch("map/route_stops.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      routeStops = j; showStops();
    }).catch(function (e) { console.log("stops failed: " + e); stopsAsked = false; });
  }

  function setSelected(routeId, dir) {
    selected = routeId || "";
    selectedDir = dir || "";
    ensureStops();
    showStops();
    loadRouteTraffic();
    ensureRouteTrips(selected).then(updateDepartures);
    ensureTripHistory(selected);
    var want = selectedDir ? ["all", ["==", ["get", "route"], selected], ["==", ["get", "dir"], selectedDir]] : ["==", ["get", "route"], selected];
    ["lines-casing", "lines-selected", "lines-flow"].forEach(function (id) {
      if (map.getLayer(id)) map.setFilter(id, want);
    });
    // with a route selected, hide every other route's line (only its own, bold, stays)
    if (map.getLayer("lines-faint")) map.setLayoutProperty("lines-faint", "visibility", selected ? "none" : "visible");
  }
  function fitRoute(routeId, dir) {
    var b = new maplibregl.LngLatBounds(), any = false;
    Object.keys(shapes || {}).forEach(function (key) {
      if (key.split("|")[0] !== routeId || (dir && key.split("|")[1] !== dir)) return;
      shapes[key].forEach(function (line) { line.forEach(function (c) { b.extend(c); any = true; }); });
    });
    if (any) map.fitBounds(b, { padding: 40, maxZoom: 14, duration: 600 });
  }

  /* ---------- the clicked bus: its popup follows it and refreshes with every update ---------- */
  function refreshTracked() {
    if (!tracked) return;
    var b = pos[tracked.id];
    if (!b) { tracked.head.innerHTML += '<br><span class="m">This bus is no longer in the feed.</span>'; return; }
    var p = propsOf(b.v, b.drawnEst ? b.beam : b.heading, b.drawnEst, b.offRoute);
    tracked.head.innerHTML = headHtml(p);
    loadPredictions(p, tracked.pred, true);
  }
  function openPopup(p, lngLat) {
    if (tracked) tracked.popup.remove();
    var box = document.createElement("div");
    box.style.minWidth = "260px";
    var head = document.createElement("div"), pred = document.createElement("div");
    head.innerHTML = headHtml(p);
    pred.className = "m";
    pred.textContent = "Loading the agency's predictions…";
    box.appendChild(head); box.appendChild(pred);
    var popup = new maplibregl.Popup({ offset: 10, maxWidth: "360px", closeOnClick: false }).setLngLat(lngLat).setDOMContent(box).addTo(map);
    tracked = { id: p.id, popup: popup, head: head, pred: pred };
    popup.on("close", function () { if (tracked && tracked.popup === popup) tracked = null; });
    loadPredictions(p, pred, false);
  }

  /* ---------- a stop: when the next buses are predicted to arrive (the agency's prediction, not the dots) ---------- */
  var stopTracked = null;
  function arrivalsHtml(p, trips, names) {
    var res = GtfsRt.arrivalsAtStop(trips, p.id), now = Date.now() / 1000;
    var mine = res.arrivals.filter(function (a) { return (!selected || a.routeId === selected) && (!selectedDir || String(a.directionId) === selectedDir); });
    var list = mine.filter(function (a) { return !a.skipped && a.time >= now - 30; }).slice(0, 4);
    var skips = mine.filter(function (a) { return a.skipped; });
    var title = "<b>" + esc(names[p.id] || "stop " + p.id) + "</b>";
    var kind = Number(p.tp) === 1 ? "Timetable stop: the schedule holds buses to a set time here." : "Regular stop: its scheduled time is calculated between timetable stops.";
    var rows = list.map(function (a) {
      var mins = Math.max(0, Math.round((a.time - now) / 60));
      var when = mins <= 0 ? "arriving now" : "in " + mins + " min";
      var mine = a.delay != null ? tripUsually(a.routeId, a.tripId, p.id, a.time - a.delay) : null;
      return "<b>" + when + "</b> (" + hhmm(a.time) + ") route " + esc(name(a.routeId)) + (a.delay != null ? ", " + lateText(a.delay) : "") +
        (mine ? '<span class="m">; this trip usually gets here at ' + hhmm(mine.at) + "</span>" : "");
    });
    var body = rows.length ? "Agency's predicted arrivals:<br>" + rows.join("<br>") : "No predicted arrivals here right now.";
    if (skips.length) body += '<br><span class="m"><b>' + skips.length + " upcoming trip" + (skips.length > 1 ? "s" : "") + " will not stop here</b> (" +
      skips.filter(function (a) { return a.canceled; }).length + " canceled, " + skips.filter(function (a) { return !a.canceled; }).length + " skipping this stop), according to the agency.</span>";
    var age = res.feedTimestamp ? now - res.feedTimestamp : 0;
    return title + '<br><span class="m">' + kind + '</span><div style="margin-top:6px">' + body + "</div>" +
      '<div class="m" style="margin-top:6px">Predictions as of ' + (res.feedTimestamp ? hhmm(res.feedTimestamp) : "?") + (age > 600 ? " (OUT OF DATE)" : "") +
      ". They use each bus's position and delay, so they are better for catching a bus than the dots, which are about a minute old.</div>";
  }
  function refreshStopPopup() {
    if (!stopTracked) return;
    Promise.all([getTrips(), getStatics(), ensureTripHistory(selected)]).then(function (res) {
      if (stopTracked) stopTracked.el.innerHTML = arrivalsHtml(stopTracked.p, res[0], res[1][0] || {});
    }).catch(function () {});
  }
  function openStopPopup(p, lngLat) {
    if (stopTracked) stopTracked.popup.remove();
    var el = document.createElement("div");
    el.style.minWidth = "240px";
    el.textContent = "Loading predictions…";
    var popup = new maplibregl.Popup({ offset: 8, maxWidth: "320px" }).setLngLat(lngLat).setDOMContent(el).addTo(map);
    stopTracked = { p: p, el: el, popup: popup };
    popup.on("close", function () { if (stopTracked && stopTracked.popup === popup) stopTracked = null; });
    refreshStopPopup();
  }

  /* ---------- live traffic along the selected route (TomTom, through our relay) ---------- */
  // The relay holds the TomTom key. From localhost, a relay running on this computer (npx wrangler dev, port 8787)
  // is used when it answers, because it has the key in a local file even before the deployed relay is updated.
  var trafficBase = null, trafficVersion = 0, trafficCache = {}, TRAFFIC_MS = 90000, MAX_POINTS = 120;
  function getTrafficBase() {
    if (trafficBase) return Promise.resolve(trafficBase);
    if (location.hostname !== "localhost" && location.hostname !== "127.0.0.1") { trafficBase = RELAY; return Promise.resolve(trafficBase); }
    var local = "http://localhost:8787";
    return fetch(local + "/memory/status").then(function (r) { trafficBase = r.ok ? local : RELAY; return trafficBase; })
      .catch(function () { trafficBase = RELAY; return trafficBase; });
  }
  /* Points every 450 m or so along the selected route's lines (both directions unless one is chosen), at most 120. */
  function trafficPoints() {
    var pts = [], seen = {};
    Object.keys(shapes || {}).forEach(function (key) {
      var parts = key.split("|");
      if (parts[0] !== selected || (selectedDir && parts[1] !== selectedDir)) return;
      shapes[key].forEach(function (line) {
        var len = Estimate.length(line), step = Math.max(450, len / 40);
        for (var d = 0; d <= len; d += step) {
          var p = Estimate.pointAt(line, d), k = p[1].toFixed(4) + "," + p[0].toFixed(4);
          if (!seen[k]) { seen[k] = true; pts.push([p[1], p[0]]); }
        }
      });
    });
    return pts.slice(0, MAX_POINTS);
  }
  function loadRouteTraffic() {
    var src = map.getSource("route-traffic"), note = document.getElementById("trafficnote"), empty = { type: "FeatureCollection", features: [] };
    if (!src) return;
    if (!trafficOn || !trafficOn.checked) { trafficVersion++; src.setData(empty); if (note) note.textContent = ""; return; }
    if (!selected) { trafficVersion++; src.setData(empty); if (note) note.textContent = "Choose a route to see the traffic along it."; return; }
    var key = selected + "|" + selectedDir, hit = trafficCache[key];
    if (hit && Date.now() - hit.at < TRAFFIC_MS) { src.setData(hit.data); if (note) note.textContent = hit.note; return; }
    var version = ++trafficVersion, pts = trafficPoints(), route = selected;
    if (note && !hit) note.textContent = "Loading traffic along route " + name(route) + "…";
    getTrafficBase().then(function (base) {
      var answers = [], next = 0;
      function worker() {
        if (next >= pts.length || version !== trafficVersion) return Promise.resolve();
        var p = pts[next++];
        return fetch(base + "/traffic/segment?lat=" + p[0].toFixed(5) + "&lon=" + p[1].toFixed(5))
          .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
          .then(function (j) { answers.push(j); return worker(); });
      }
      var workers = []; for (var w = 0; w < 6; w++) workers.push(worker());
      return Promise.all(workers).then(function () {
        if (version !== trafficVersion) return; // the route changed while we were asking
        var feats = [], seenSeg = {}, ok = 0, slowest = null, closed = 0, routeLines = [];
        Object.keys(shapes || {}).forEach(function (key) {
          var parts = key.split("|");
          if (parts[0] === route && (!selectedDir || parts[1] === selectedDir)) routeLines = routeLines.concat(shapes[key]);
        });
        answers.forEach(function (s) {
          if (!s || s.none || !s.coords || s.coords.length < 2 || !(s.free > 0)) return;
          ok++;
          var k = s.coords[0].join(",") + ">" + s.coords[s.coords.length - 1].join(",");
          if (seenSeg[k]) return;
          seenSeg[k] = true;
          var ratio = s.closed ? 0 : Math.min(1.2, s.cur / s.free);
          // TomTom answers with the whole road stretch, which runs on past the route and can be a cross street:
          // keep only the parts that run along the chosen route (within 30 m, within 35 degrees of its direction)
          var runs = Estimate.clipAlong(s.coords, routeLines, 30, 35);
          if (!runs.length) return;
          if (s.closed) closed++;
          else if (slowest == null || ratio < slowest) slowest = ratio;
          runs.forEach(function (c) { feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: c }, properties: { ratio: ratio, closed: s.closed ? 1 : 0 } }); });
        });
        var data = { type: "FeatureCollection", features: feats };
        var text = ok === 0 ? "No traffic information for route " + name(route) + " right now." :
          "Traffic along route " + name(route) + " (TomTom, " + ok + " of " + pts.length + " points): green normal speed, yellow a little slow, orange slow, red very slow" +
          (slowest != null ? "; the slowest stretch is moving at " + Math.round(slowest * 100) + "% of its normal speed" : "") +
          (closed ? "; " + closed + " stretch" + (closed > 1 ? "es" : "") + " marked closed (dark red)" : "") + ". Refreshes every 90 seconds.";
        trafficCache[key] = { at: Date.now(), data: data, note: text };
        src.setData(data);
        if (note) note.textContent = text;
      });
    });
  }
  setInterval(function () { if (!document.hidden) loadRouteTraffic(); }, TRAFFIC_MS);

  /* ---------- next departures from the start of the route: is a bus there? ---------- */
  // For the next trips that have not started, look at the trip the same bus runs just before (the timetable's block)
  // and ask the relay's trip memory what happened to it: still running, finished near this trip's start (a bus is
  // waiting), finished elsewhere, or never seen (then the next trip often does not run either: 84% on 2026-10-20).
  var depBusy = false, WAIT_RADIUS_M = 250, AVG_SPEED_MS = 5.5; // about 20 km/h, a typical city bus speed including stops
  function metresBetween(a, b) {
    var kx = 111320 * Math.cos(a[1] * Math.PI / 180);
    return Math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110540);
  }
  function updateDepartures() {
    var el = document.getElementById("departures"), src = map.getSource("waiting");
    var empty = { type: "FeatureCollection", features: [] };
    if (!el) return;
    if (!selected) { el.innerHTML = ""; if (src) src.setData(empty); return; }
    var route = selected, dir = selectedDir, key = route + "|" + dir;
    // a result is only thrown away if the route or direction changed meanwhile; one refresh at a time
    var stale = function () { return selected + "|" + selectedDir !== key; };
    if (depBusy === key) return;
    depBusy = key;
    var done = function () { if (depBusy === key) depBusy = false; };
    Promise.all([ensureRouteTrips(route), getTrips(), getStatics(), getTrafficBase()]).then(function (res) {
      var data = res[0], names = res[2][0] || {}, base = res[3];
      if (!data || stale()) return done();
      var now = Date.now() / 1000, running = {};
      Object.keys(pos).forEach(function (id) { if (pos[id].v.tripId) running[pos[id].v.tripId] = pos[id]; });
      var upcoming = GtfsRt.tripsForRoute(res[1], route).trips.map(function (t) {
        var info = data.trips[t.tripId];
        if (!info || running[t.tripId]) return null;
        if (dir && String(info[0]) !== dir) return null;
        var first = info[5].split(";")[0].split(":"), start = serviceBase(t.startDate || todayPacific()) + Number(first[1]);
        if (start < now - 600 || start > now + 3 * 3600) return null;
        return { t: t, info: info, start: start, firstStop: first[0], dir: String(info[0]) };
      }).filter(Boolean).sort(function (a, b) { return a.start - b.start; });
      var perDir = {}, pick = [];
      upcoming.forEach(function (u) { perDir[u.dir] = (perDir[u.dir] || 0) + 1; if (perDir[u.dir] <= (dir ? 3 : 2)) pick.push(u); });
      if (!pick.length) { el.innerHTML = '<span class="note">No upcoming departures from the start of this route in the agency\'s file.</span>'; if (src) src.setData(empty); return done(); }
      var prevIds = pick.map(function (u) { return u.info[1]; }).filter(Boolean);
      return fetch(base + "/memory/ended?trips=" + encodeURIComponent(prevIds.join(","))).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
        .then(function (mem) {
          done();
          if (stale()) return;
          var feats = [], lines = pick.map(function (u) {
            var info = u.info, prev = info[1], prevLast = info[3], prevEnd = info[4] == null ? null : serviceBase(u.t.startDate || todayPacific()) + info[4];
            var startName = esc(names[u.firstStop] || "stop " + u.firstStop), at = data.stops[u.firstStop];
            var head = "<b>" + hhmm(u.start) + "</b> toward " + esc(heads[route + "|" + u.dir] || "direction " + u.dir) + ": ";
            var status, kind;
            var skippedFirst = 0;
            for (var i = 0; i < u.t.updates.length && u.t.updates[i].skipped; i++) skippedFirst++;
            var joinAt = skippedFirst ? u.t.updates.filter(function (x) { return !x.skipped && (x.arrTime || x.depTime); })[0] : null;
            if (u.t.canceled) { status = "the agency has marked this trip canceled."; kind = "missing"; }
            else if (skippedFirst) { status = "skips its first " + skippedFirst + " stops" + (joinAt ? "; joins at " + esc(names[joinAt.stopId] || "stop " + joinAt.stopId) + " at about " + hhmm(joinAt.arrTime || joinAt.depTime) : "") + "."; kind = "partial"; }
            else if (!prev) { status = "the bus's first trip of the day (it comes from the base); no earlier trip to check."; kind = "unknown"; }
            else if (running[prev] || (mem && mem.running.indexOf(prev) >= 0)) { status = "the bus is still on its previous trip" + (running[prev] ? " (shown on the map)" : "") + "."; kind = "coming"; }
            else if (mem && mem.ended[prev]) {
              // The agency's feed stops showing a trip when its SCHEDULED time runs out (measured 2026-10-20: 90 to 97% of
              // trips that went quiet more than 250 m before their last stop did so within a minute of the scheduled
              // end). So a bus on time goes quiet at the stop, and a late one is cut off while still driving there.
              var e = mem.ended[prev], end = data.stops[prevLast];
              var gap = end && e.lon != null ? metresBetween([e.lon, e.lat], end) : null;
              var gapText = gap == null ? "" : gap < 1000 ? "about " + Math.round(gap / 50) * 50 + " m" : (gap / 1000).toFixed(1) + " km";
              if (gap != null && gap <= WAIT_RADIUS_M) {
                status = "<b>a bus should be waiting at " + startName + "</b> (its previous trip ended " + gapText + " from its last stop)."; kind = "waiting";
              } else if (gap != null) {
                // time still needed: the distance at a typical bus speed, minus the time since that last report
                var left = gap / AVG_SPEED_MS - Math.max(0, now - (e.ts || e.endedAt)), mins = Math.round(left / 60);
                var tail = " from its last stop when the agency stopped showing it (that happens at the trip's scheduled end)";
                if (left <= 60) {
                  status = "<b>a bus has probably reached " + startName + " by now</b>: its previous trip was running late and was " + gapText + tail + "."; kind = "waiting";
                } else {
                  status = "<b>the bus is on its way to " + startName + "</b>, running late: its previous trip was still " + gapText + tail +
                    ", so roughly " + mins + " more minute" + (mins > 1 ? "s" : "") + " to get there."; kind = "coming";
                }
              } else {
                status = "its previous trip has ended; we can't place the bus, so a bus at " + startName + " is not confirmed."; kind = "unknown";
              }
            } else if (mem && mem.since && prevEnd && prevEnd < now - 300 && mem.since < prevEnd - 900) {
              status = "<b>the bus's previous trip never showed up.</b> This trip may not run normally (after a missing trip, the next one was also missing 84% of the time).";
              kind = "missing";
            } else if (prevEnd && prevEnd > now) { status = "the bus's previous trip is not due to finish until " + hhmm(prevEnd) + "."; kind = "coming"; }
            else { status = "not known (the trip memory started at " + (mem && mem.since ? hhmm(mem.since) : "an unknown time") + ")."; kind = "unknown"; }
            if (at) feats.push({ type: "Feature", geometry: { type: "Point", coordinates: at }, properties: { kind: kind, label: hhmm(u.start) + (kind === "waiting" ? " bus waiting" : kind === "missing" ? " no bus known" : "") } });
            return head + status;
          });
          el.innerHTML = '<b>Next departures from the start</b> <span class="note">(is a bus there?)</span><br>' + lines.join("<br>");
          if (src) src.setData({ type: "FeatureCollection", features: feats });
        });
    }).catch(function (e) { done(); console.log("departures error: " + (e && e.stack ? e.stack.split("\n").slice(0, 3).join(" / ") : e)); });
  }
  setInterval(function () { if (!document.hidden && selected) updateDepartures(); }, 30000);

  // for checking the page from a script (and from the browser console): open the popup of a bus by its id
  window.transitOpenBus = function (id) {
    var b = pos[id];
    if (!b) return false;
    openPopup(propsOf(b.v, b.drawnEst ? b.beam : b.heading, b.drawnEst, b.offRoute), [b.lon, b.lat]);
    return true;
  };

  function fillRoutePicker(feed) {
    var ids = {};
    feed.vehicles.forEach(function (v) { if (v.routeId) ids[v.routeId] = true; });
    var keep = pick.value;
    var sorted = Object.keys(ids).sort(function (a, b) {
      return String(name(a)).localeCompare(String(name(b)), undefined, { numeric: true });
    });
    pick.length = 1;
    sorted.forEach(function (id) { var o = document.createElement("option"); o.value = id; o.textContent = name(id); pick.appendChild(o); });
    pick.value = keep;
  }

  function refresh() {
    fetch(RELAY + "/vehicles", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("relay answered " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      last = GtfsRt.decodeVehicles(new Uint8Array(buf));
      var age = Date.now() / 1000 - last.timestamp;
      if (age > STALE_S) setState("warn", "The agency's feed is " + ago(age) + " old (as of " + seattle(last.timestamp) + ")");
      else setState("good", "Live, updates by itself: agency feed from " + seattle(last.timestamp));
      if (pick.length === 1) fillRoutePicker(last);
      receive(last);
      refreshTracked();
      refreshStopPopup();
    }).catch(function (e) {
      setState("bad", "Can't reach the live feed (" + e.message + "). Showing the last positions received" + (last ? " from " + seattle(last.timestamp) : "") + ". Trying again automatically.");
    });
  }

  map.on("load", function () {
    map.addSource("lines", { type: "geojson", data: lineFeatures() });
    map.addLayer({ id: "lines-faint", type: "line", source: "lines", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 1.6, "line-opacity": 0.4 } });
    map.addLayer({ id: "lines-casing", type: "line", source: "lines", filter: ["==", ["get", "route"], ""], layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": 8, "line-opacity": 0.9 } });
    map.addLayer({ id: "lines-selected", type: "line", source: "lines", filter: ["==", ["get", "route"], ""], layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 5 } });
    map.addImage("arrow", Markers.image("chevron"), { sdf: true, pixelRatio: 2 });
    map.addImage("beam", Markers.image("beam"), { sdf: true, pixelRatio: 2 });
    // arrows along the highlighted route, pointing the way traffic flows (the line runs in the direction of travel)
    map.addLayer({ id: "lines-flow", type: "symbol", source: "lines", filter: ["==", ["get", "route"], ""],
      layout: { "symbol-placement": "line", "symbol-spacing": 90, "icon-image": "arrow", "icon-rotate": 0, "icon-size": 0.5, "icon-allow-overlap": true, "icon-ignore-placement": true },
      paint: { "icon-color": "#ffffff", "icon-opacity": 0.95 } });
    // Live traffic along the selected route (filled in by loadRouteTraffic): each road stretch coloured by how its
    // speed now compares with its normal speed. Under the stops and buses, over the highlighted route line.
    map.addSource("route-traffic", { type: "geojson", data: { type: "FeatureCollection", features: [] }, attribution: "Traffic © TomTom" });
    map.addLayer({ id: "route-traffic-casing", type: "line", source: "route-traffic", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 5, 16, 11] } });
    map.addLayer({ id: "route-traffic", type: "line", source: "route-traffic", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-width": ["interpolate", ["linear"], ["zoom"], 11, 3, 16, 7],
        "line-color": ["case", ["==", ["get", "closed"], 1], "#5a1414",
          ["step", ["get", "ratio"], "#d63a2f", 0.4, "#f08a24", 0.65, "#f2c230", 0.85, "#2e9e4f"]] } });
    map.addSource("route-stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    // stops of the selected route: small white rings with a dark outline, far smaller than a bus. Click one to see
    // its name, whether it is a timetable stop, and the next predicted arrivals.
    map.addLayer({ id: "route-stops", type: "circle", source: "route-stops", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.8, 14, 3.2, 17, 4.8],
      "circle-color": "#ffffff", "circle-stroke-color": "#1d2330", "circle-stroke-width": 1.3 } });
    map.addSource("waiting", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "waiting", type: "circle", source: "waiting", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 5, 16, 9], "circle-color": "#ffffff",
      "circle-stroke-width": 3, "circle-stroke-color": ["match", ["get", "kind"], "waiting", "#2e9e4f", "missing", "#d63a2f", "partial", "#f08a24", "#9aa0a6"] } });
    map.addLayer({ id: "waiting-label", type: "symbol", source: "waiting", minzoom: 12,
      layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.2], "text-anchor": "top" },
      paint: { "text-color": "#1d2330", "text-halo-color": "#ffffff", "text-halo-width": 1.5 } });
    map.addSource("bus-ghosts", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "bus-trail", type: "line", source: "bus-ghosts", filter: ["==", ["geometry-type"], "LineString"],
      paint: { "line-color": ["get", "color"], "line-width": 2.5, "line-opacity": 0.8, "line-dasharray": [1, 1.4] } });
    map.addLayer({ id: "bus-ghost", type: "circle", source: "bus-ghosts", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 3, 16, 5.5], "circle-color": "#ffffff", "circle-opacity": 0.9,
        "circle-stroke-color": ["get", "color"], "circle-stroke-width": 1.8 } });
    map.addLayer({ id: "bus-ghost-age", type: "symbol", source: "bus-ghosts", minzoom: 13, filter: ["==", ["geometry-type"], "Point"],
      layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top", "text-allow-overlap": false },
      paint: { "text-color": "#1d2330", "text-halo-color": "#ffffff", "text-halo-width": 1.5 } });
    map.addSource("buses", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    // A bus is a disc in its route colour with a white outline. When its heading is known a faint wedge (a "beam")
    // fans out in front of it, like the location dot in Apple Maps; with no known heading there is no beam.
    var BODY = ["interpolate", ["linear"], ["zoom"], 9, 4.5, 12, 6, 15, 7, 17, 8];
    var SIZE = ["interpolate", ["linear"], ["zoom"], 9, 0.8, 12, 1.1, 15, 1.3, 17, 1.5];
    var disc = { "circle-radius": BODY, "circle-color": ["case", ["==", ["get", "offroute"], 1], "#9aa0a6", ["get", "color"]],
      "circle-opacity": ["case", ["==", ["get", "offroute"], 1], 0.75, 1], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.8 };
    map.addLayer({ id: "bus-beam", type: "symbol", source: "buses", filter: ["all", [">=", ["get", "heading"], 0], ["!=", ["get", "offroute"], 1]], layout: {
      "icon-image": "beam", "icon-rotate": ["get", "heading"], "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true, "icon-size": SIZE },
      paint: { "icon-color": ["get", "color"], "icon-opacity": 0.45 } });
    map.addLayer({ id: "bus-dots", type: "circle", source: "buses", paint: disc });
    var busLayers = ["bus-dots"];
    busLayers.forEach(function (layer) {
      map.on("click", layer, function (e) {
        var p = e.features[0].properties;
        if (!filter) setSelected(p.route, "");
        openPopup(p, e.lngLat);
      });
      map.on("mouseenter", layer, function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, function () { map.getCanvas().style.cursor = ""; });
    });
    map.on("click", "route-stops", function (e) { openStopPopup(e.features[0].properties, e.lngLat); });
    map.on("mouseenter", "route-stops", function () { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "route-stops", function () { map.getCanvas().style.cursor = ""; });
    map.on("click", function (e) {
      if (map.queryRenderedFeatures(e.point, { layers: busLayers.concat(["route-stops"]) }).length) return;
      if (!filter) setSelected("", "");
    });
    refresh();
    timer = setInterval(function () { if (!document.hidden) refresh(); }, POLL_MS);
  });

  function fillDirectionPicker(routeId) {
    dirPick.length = 1;
    dirPick.value = "";
    dirFilter = "";
    var dirs = Object.keys(shapes || {}).filter(function (k) { return k.split("|")[0] === routeId; })
      .map(function (k) { return k.split("|")[1]; }).sort();
    dirs.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d; o.textContent = "toward " + (heads[routeId + "|" + d] || "direction " + d);
      dirPick.appendChild(o);
    });
    dirPick.disabled = !routeId || dirs.length < 1;
  }
  pick.addEventListener("change", function () {
    filter = pick.value;
    fillDirectionPicker(filter);
    if (!filter && tracked) tracked.popup.remove();
    setSelected(filter, "");
    if (filter) fitRoute(filter, "");
    startAnimation();
    render();
  });
  dirPick.addEventListener("change", function () {
    dirFilter = dirPick.value;
    setSelected(filter, dirFilter);
    fitRoute(filter, dirFilter);
    startAnimation();
    render();
  });
  if (estimateOn) estimateOn.addEventListener("change", function () { startAnimation(); render(); });
  setInterval(showChecks, 5000);
  if (trafficOn) {
    trafficOn.disabled = false;
    trafficOn.addEventListener("change", function () {
      loadRouteTraffic();
    });
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden && timer) refresh(); });

  // route lines and route numbers: the page still works (dots only, route ids) if these files are missing
  fetch("map/shapes.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
    shapes = j; var src = map.getSource("lines"); if (src && j) src.setData(lineFeatures());
    Object.keys(pos).forEach(function (id) { pos[id].heading = headingOf(pos[id].v); }); if (filter) fillDirectionPicker(filter); render();
  }).catch(function () {});
  fetch("map/headsigns.json").then(function (r) { return r.ok ? r.json() : {}; }).then(function (j) { heads = j; }).catch(function () {});
  fetch("map/routes.json").then(function (r) { return r.ok ? r.json() : {}; }).then(function (j) {
    routes = j; if (last) { fillRoutePicker(last); render(); }
  }).catch(function () {});
})();
