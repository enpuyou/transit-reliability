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
  var OFFSET_MS = 4000;   // after a new report the drawn bus eases from where it was to the new estimate over this long
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
    if (p.ts) {
      var age = Math.max(0, Date.now() / 1000 - Number(p.ts));
      bits.push('<span class="m">position reported ' + seattle(Number(p.ts)) + " (" + ago(age) + " ago)" +
        (Number(p.est) === 1 ? "; the dot is an estimate: the bus is moved forward along its route at 75% of its recent speed since then" : (age > 45 ? "; the bus has likely moved on since, a bus can cover about 100 m in 10 s" : "")) + "</span>");
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
    Promise.all([getTrips(), getStatics()]).then(function (res) {
      var trip = GtfsRt.findTripUpdate(res[0], p.trip), stops = res[1][0] || {}, hist = (res[1][1] || {}).h || {};
      if (!trip) { if (!quiet || !el.querySelector("b")) el.textContent = "The agency has no prediction for this trip right now."; return; }
      var next = trip.updates.filter(function (u) { return u.stopSeq >= Number(p.stopSeq) && u.arrTime; });
      var keyOf = function (u) { return p.route + "|" + p.dir + "|" + u.stopId; };
      var shown = next.slice(0, 3);
      var tp = next.filter(function (u) { return hist[keyOf(u)]; })[0];
      if (tp && shown.indexOf(tp) < 0) shown.push(tp);
      if (!shown.length) { el.textContent = "No upcoming stops in the agency's predictions."; return; }
      var lastStop = trip.updates.length ? trip.updates[trip.updates.length - 1].stopId : null;
      var rows = shown.map(function (u) {
        var sched = u.arrDelay != null ? u.arrTime - u.arrDelay : null, b = sched ? bucket(sched) : null;
        var h = b && hist[keyOf(u)] && hist[keyOf(u)][b.key];
        var line = "<b>" + esc(stops[u.stopId] || "stop " + u.stopId) + "</b><br>" +
          "Agency predicts <b>" + hhmm(u.arrTime) + "</b>" + (sched ? ", timetable " + hhmm(sched) + " (" + lateText(u.arrDelay) + ")" : "");
        if (h) line += '<br><span class="m">Usually ' + lateText(h[0]) + " here on " + b.text + "; 1 trip in 7 is " + minutes(h[1]) + (h[1] > 0 ? " late or more" : " early or less") + " (" + h[2] + " trips).</span>";
        return line;
      });
      var age = trip.feedTimestamp ? Date.now() / 1000 - trip.feedTimestamp : 0;
      el.innerHTML = (lastStop && stops[lastStop] ? "<b>Toward " + esc(stops[lastStop]) + "</b><hr style='border:0;border-top:1px solid #dcdfe6;margin:6px 0'>" : "") + rows.join("<hr style='border:0;border-top:1px solid #dcdfe6;margin:6px 0'>") +
        '<div class="m" style="margin-top:6px">Predictions as of ' + hhmm(trip.feedTimestamp) + (age > 600 ? " — OUT OF DATE, ignore them" : "") +
        ". History is kept only for stops with a timetable time, and comes from past days only (" + ((res[1][1] || {}).first_day || "?") + " to " + ((res[1][1] || {}).last_day || "?") + "); our arrival times are estimates from GPS.</div>";
    }).catch(function (err) { if (!quiet) el.textContent = "Couldn't load predictions (" + err.message + ")."; });
  }

  // ?base=none draws no background map (used for automated screenshots, where the tile server is not reachable)
  var BLANK = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#e9ecef" } }] };
  var map = new maplibregl.Map({ container: "livemap", style: new URLSearchParams(location.search).get("base") === "none" ? BLANK : STYLE, center: [-122.33, 47.6], zoom: 10.5, attributionControl: { compact: true } });
  window.transitMap = map; // handy for debugging in the browser console
  map.on("error", function (e) {
    console.log("map error: " + (e && e.error && e.error.message ? e.error.message : e));
    var note = document.getElementById("trafficnote");
    if (e && e.sourceId === "traffic" && note && trafficOn && trafficOn.checked) note.textContent = "Traffic is not available right now.";
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

  /* ---------- bus positions: every bus glides from where it was drawn to where it now is ---------- */
  function propsOf(v, heading, est) {
    return { id: v.id || "", route: v.routeId || "", color: colour(v.routeId), label: name(v.routeId), speed: v.speed,
      stopSeq: v.stopSeq, ts: v.ts, trip: v.tripId || "", dir: v.directionId, heading: heading == null ? -1 : heading, est: est ? 1 : 0 };
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
  function estDist(b, nowSec) { return Estimate.estimate(b.rep, b.vel, nowSec, b.len); }
  /* Called for every bus on every update. When the bus has a NEW report: (1) check the estimates we drew earlier
     against where the bus really was at those moments (the report carries the time its GPS fix was taken, so a late
     delivery does not matter), (2) remember the report, (3) work out its speed. */
  function track(b, v, now) {
    if (!shapes || !v.ts) return;
    var fresh = !b.rep || v.ts !== b.rep.ts;
    if (fresh) {
      var hit = lineFor(v);
      if (!hit) { b.est = false; b.rep = null; b.hist = []; b.preds = []; return; }
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
      b.hist.push(b.rep); if (b.hist.length > 5) b.hist.shift();
      b.vel = Estimate.speed(b.hist);
      // ease from where the bus is drawn now to the new estimate instead of jumping
      if (b.est && b.vel != null) { var e = Estimate.pointAt(b.line, estDist(b, wallSeconds())); b.offLon = b.lon - e[0]; b.offLat = b.lat - e[1]; b.offT0 = now; }
    }
    b.est = b.vel != null && !!b.rep;
    if (b.est) { var w = wallSeconds(); b.preds.push({ at: w, est: estDist(b, w), raw: b.rep.dist }); if (b.preds.length > 14) b.preds.shift(); }
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
        // keep moving the bus along its route at 75% of its recent speed since the report
        var e = Estimate.pointAt(b.line, estDist(b, nowSec)), k = b.offT0 == null ? 0 : Math.max(0, 1 - (now - b.offT0) / OFFSET_MS);
        b.lon = e[0] + (b.offLon || 0) * k; b.lat = e[1] + (b.offLat || 0) * k; b.beam = e[2]; b.drawnEst = true;
        moving = true;
      } else {
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
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [b.lon, b.lat] }, properties: propsOf(b.v, b.drawnEst ? b.beam : b.heading, b.drawnEst) });
    });
    var src = map.getSource("buses");
    if (src) src.setData({ type: "FeatureCollection", features: feats });
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
    var p = propsOf(b.v, b.drawnEst ? b.beam : b.heading, b.drawnEst);
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
    var list = res.arrivals.filter(function (a) {
      return a.time >= now - 30 && (!selected || a.routeId === selected) && (!selectedDir || String(a.directionId) === selectedDir);
    }).slice(0, 4);
    var title = "<b>" + esc(names[p.id] || "stop " + p.id) + "</b>";
    var kind = Number(p.tp) === 1 ? "Timetable stop: the schedule holds buses to a set time here." : "Regular stop: its scheduled time is calculated between timetable stops.";
    var rows = list.map(function (a) {
      var mins = Math.max(0, Math.round((a.time - now) / 60));
      var when = mins <= 0 ? "arriving now" : "in " + mins + " min";
      return "<b>" + when + "</b> (" + hhmm(a.time) + ") route " + esc(name(a.routeId)) + (a.delay != null ? ", " + lateText(a.delay) : "");
    });
    var body = rows.length ? "Agency's predicted arrivals:<br>" + rows.join("<br>") : "No predicted arrivals here right now.";
    var age = res.feedTimestamp ? now - res.feedTimestamp : 0;
    return title + '<br><span class="m">' + kind + '</span><div style="margin-top:6px">' + body + "</div>" +
      '<div class="m" style="margin-top:6px">Predictions as of ' + (res.feedTimestamp ? hhmm(res.feedTimestamp) : "?") + (age > 600 ? " (OUT OF DATE)" : "") +
      ". They use each bus's position and delay, so they are better for catching a bus than the dots, which are about a minute old.</div>";
  }
  function refreshStopPopup() {
    if (!stopTracked) return;
    Promise.all([getTrips(), getStatics()]).then(function (res) {
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
    // Live traffic: TomTom's flow tiles are transparent PNGs coloured by how fast traffic moves compared with free flow.
    // They come through our relay, which holds the TomTom key and remembers each tile for a minute and a half, so the
    // key never reaches the browser. They sit under the route lines and buses.
    map.addSource("traffic", { type: "raster", tileSize: 512, minzoom: 9, maxzoom: 17, attribution: "Traffic © TomTom",
      tiles: [RELAY + "/traffic/{z}/{x}/{y}.png"] });
    map.addLayer({ id: "traffic", type: "raster", source: "traffic", layout: { visibility: trafficOn && trafficOn.checked ? "visible" : "none" }, paint: { "raster-opacity": 0.85 } }, "lines-faint");
    map.addSource("route-stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    // stops of the selected route: small white rings with a dark outline, far smaller than a bus. Click one to see
    // its name, whether it is a timetable stop, and the next predicted arrivals.
    map.addLayer({ id: "route-stops", type: "circle", source: "route-stops", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.8, 14, 3.2, 17, 4.8],
      "circle-color": "#ffffff", "circle-stroke-color": "#1d2330", "circle-stroke-width": 1.3 } });
    map.addSource("buses", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    // A bus is a disc in its route colour with a white outline. When its heading is known a faint wedge (a "beam")
    // fans out in front of it, like the location dot in Apple Maps; with no known heading there is no beam.
    var BODY = ["interpolate", ["linear"], ["zoom"], 9, 4.5, 12, 6, 15, 7, 17, 8];
    var SIZE = ["interpolate", ["linear"], ["zoom"], 9, 0.8, 12, 1.1, 15, 1.3, 17, 1.5];
    var disc = { "circle-radius": BODY, "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.8 };
    map.addLayer({ id: "bus-beam", type: "symbol", source: "buses", filter: [">=", ["get", "heading"], 0], layout: {
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
      if (map.getLayer("traffic")) map.setLayoutProperty("traffic", "visibility", trafficOn.checked ? "visible" : "none");
      var note = document.getElementById("trafficnote");
      if (note && !trafficOn.checked) note.textContent = "";
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
