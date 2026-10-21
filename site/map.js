/* Live bus map. Every POLL_MS the browser asks the relay Worker for the agency's vehicle file, decodes it
   (gtfsrt.js) and glides each bus dot to its new position. Route lines come from map/shapes.json. An open
   popup follows its bus and refreshes by itself. No Databricks involved: the page only talks to the relay,
   the map tiles and files on this site. */
(function () {
  "use strict";
  // Where the relay Worker lives. Override for local testing with  map.html?relay=http://localhost:8787
  var RELAY = new URLSearchParams(location.search).get("relay") || "https://transit-relay.youenpu.workers.dev";
  var POLL_MS = 10000, GLIDE_MS = 9000, FRAME_MS = 100, SNAP_DEG = 0.01, STALE_S = 120, STYLE = "https://tiles.openfreemap.org/styles/liberty";

  var stateEl = document.getElementById("state"), countEl = document.getElementById("count"), pick = document.getElementById("routepick");
  var routes = {}, last = null, timer = null, filter = "", selected = "", shapes = null, tracked = null;
  var pos = {}, animating = false, lastFrame = 0;

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
    if (p.ts) bits.push('<span class="m">last report ' + seattle(Number(p.ts)) + " (" + ago(Math.max(0, Date.now() / 1000 - Number(p.ts))) + " ago)</span>");
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

  var map = new maplibregl.Map({ container: "livemap", style: STYLE, center: [-122.33, 47.6], zoom: 10.5, attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

  /* ---------- bus positions: every bus glides from where it was drawn to where it now is ---------- */
  function propsOf(v, heading) {
    return { id: v.id || "", route: v.routeId || "", color: colour(v.routeId), label: name(v.routeId), speed: v.speed,
      stopSeq: v.stopSeq, ts: v.ts, trip: v.tripId || "", dir: v.directionId, heading: heading == null ? -1 : heading };
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
      if (!cur) { pos[v.id] = { lon: v.lon, lat: v.lat, fx: v.lon, fy: v.lat, tx: v.lon, ty: v.lat, t0: now, v: v, heading: headingOf(v) }; return; }
      var snap = Math.abs(cur.lon - v.lon) > SNAP_DEG || Math.abs(cur.lat - v.lat) > SNAP_DEG;
      cur.fx = snap ? v.lon : cur.lon; cur.fy = snap ? v.lat : cur.lat;
      cur.tx = v.lon; cur.ty = v.lat; cur.t0 = now; cur.v = v; cur.heading = headingOf(v);
    });
    Object.keys(pos).forEach(function (id) { if (!seen[id]) delete pos[id]; });
    startAnimation();
  }
  function step(now) {
    var moving = false;
    Object.keys(pos).forEach(function (id) {
      var b = pos[id], t = Math.min(1, (now - b.t0) / GLIDE_MS);
      b.lon = b.fx + (b.tx - b.fx) * t; b.lat = b.fy + (b.ty - b.fy) * t;
      if (t < 1) moving = true;
    });
    return moving;
  }
  function render() {
    var feats = [];
    Object.keys(pos).forEach(function (id) {
      var b = pos[id];
      if (filter && b.v.routeId !== filter) return;
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [b.lon, b.lat] }, properties: propsOf(b.v, b.heading) });
    });
    var src = map.getSource("buses");
    if (src) src.setData({ type: "FeatureCollection", features: feats });
    countEl.textContent = feats.length + " buses shown";
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
      var route = key.split("|")[0];
      shapes[key].forEach(function (line) {
        feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: line }, properties: { route: route, color: colour(route) } });
      });
    });
    return { type: "FeatureCollection", features: feats };
  }
  /* A small arrow pointing up (north), drawn white so the map can colour it per route (an "SDF" image). */
  function arrowImage() {
    var n = 48, c = document.createElement("canvas"); c.width = c.height = n;
    var g = c.getContext("2d");
    g.fillStyle = "#ffffff";
    g.beginPath(); g.moveTo(n / 2, 3); g.lineTo(n - 8, n - 8); g.lineTo(n / 2, n - 17); g.lineTo(8, n - 8); g.closePath(); g.fill();
    return g.getImageData(0, 0, n, n);
  }
  function setSelected(routeId) {
    selected = routeId || "";
    ["lines-casing", "lines-selected", "lines-flow"].forEach(function (id) {
      if (map.getLayer(id)) map.setFilter(id, ["==", ["get", "route"], selected]);
    });
  }
  function fitRoute(routeId) {
    var b = new maplibregl.LngLatBounds(), any = false;
    Object.keys(shapes || {}).forEach(function (key) {
      if (key.split("|")[0] !== routeId) return;
      shapes[key].forEach(function (line) { line.forEach(function (c) { b.extend(c); any = true; }); });
    });
    if (any) map.fitBounds(b, { padding: 40, maxZoom: 14, duration: 600 });
  }

  /* ---------- the clicked bus: its popup follows it and refreshes with every update ---------- */
  function refreshTracked() {
    if (!tracked) return;
    var b = pos[tracked.id];
    if (!b) { tracked.head.innerHTML += '<br><span class="m">This bus is no longer in the feed.</span>'; return; }
    var p = propsOf(b.v, b.heading);
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
    map.addImage("arrow", arrowImage(), { sdf: true, pixelRatio: 2 });
    // arrows along the highlighted route, pointing the way traffic flows (the line runs in the direction of travel)
    map.addLayer({ id: "lines-flow", type: "symbol", source: "lines", filter: ["==", ["get", "route"], ""],
      layout: { "symbol-placement": "line", "symbol-spacing": 90, "icon-image": "arrow", "icon-rotate": 90, "icon-size": 0.45, "icon-allow-overlap": true, "icon-ignore-placement": true },
      paint: { "icon-color": "#ffffff", "icon-opacity": 0.95 } });
    map.addSource("buses", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    // a bus is an arrow pointing the way it is heading; a plain dot when its heading is unknown
    map.addLayer({ id: "bus-dots", type: "circle", source: "buses", filter: ["<", ["get", "heading"], 0], paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3, 14, 8],
      "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.2 } });
    map.addLayer({ id: "bus-arrows", type: "symbol", source: "buses", filter: [">=", ["get", "heading"], 0], layout: {
      "icon-image": "arrow", "icon-rotate": ["get", "heading"], "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true,
      "icon-size": ["interpolate", ["linear"], ["zoom"], 9, 0.4, 14, 0.85] },
      paint: { "icon-color": ["get", "color"], "icon-halo-color": "#ffffff", "icon-halo-width": 1.6 } });
    var busLayers = ["bus-dots", "bus-arrows"];
    busLayers.forEach(function (layer) {
      map.on("click", layer, function (e) {
        var p = e.features[0].properties;
        setSelected(p.route);
        openPopup(p, e.lngLat);
      });
      map.on("mouseenter", layer, function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, function () { map.getCanvas().style.cursor = ""; });
    });
    map.on("click", function (e) {
      if (map.queryRenderedFeatures(e.point, { layers: busLayers }).length) return;
      if (!filter) setSelected("");
    });
    refresh();
    timer = setInterval(function () { if (!document.hidden) refresh(); }, POLL_MS);
  });

  pick.addEventListener("change", function () {
    filter = pick.value;
    setSelected(filter);
    if (filter) fitRoute(filter);
    render();
  });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && timer) refresh(); });

  // route lines and route numbers: the page still works (dots only, route ids) if these files are missing
  fetch("map/shapes.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
    shapes = j; var src = map.getSource("lines"); if (src && j) src.setData(lineFeatures());
    Object.keys(pos).forEach(function (id) { pos[id].heading = headingOf(pos[id].v); }); render();
  }).catch(function () {});
  fetch("map/routes.json").then(function (r) { return r.ok ? r.json() : {}; }).then(function (j) {
    routes = j; if (last) { fillRoutePicker(last); render(); }
  }).catch(function () {});
})();
