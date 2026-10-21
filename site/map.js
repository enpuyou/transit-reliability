/* Live bus map. Every POLL_MS the browser asks the relay Worker for the agency's vehicle file, decodes it
   (gtfsrt.js) and moves the dots. No Databricks involved: the page only talks to the relay and the map tiles. */
(function () {
  "use strict";
  // Where the relay Worker lives. Override for local testing with  map.html?relay=http://localhost:8787
  var RELAY = new URLSearchParams(location.search).get("relay") || "https://transit-relay.youenpu.workers.dev";
  var POLL_MS = 20000, STALE_S = 120, STYLE = "https://tiles.openfreemap.org/styles/liberty";

  var stateEl = document.getElementById("state"), countEl = document.getElementById("count"), pick = document.getElementById("routepick");
  var routes = {}, last = null, timer = null, filter = "";

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
    if (!statics) statics = Promise.all(["map/stops.json", "map/stop_history.json"].map(function (u) {
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

  function loadPredictions(p, el) {
    Promise.all([getTrips(), getStatics()]).then(function (res) {
      var trip = GtfsRt.findTripUpdate(res[0], p.trip), stops = res[1][0] || {}, hist = (res[1][1] || {}).h || {};
      if (!trip) { el.textContent = "The agency has no prediction for this trip right now."; return; }
      var next = trip.updates.filter(function (u) { return u.stopSeq >= Number(p.stopSeq) && u.arrTime; });
      var keyOf = function (u) { return p.route + "|" + p.dir + "|" + u.stopId; };
      var shown = next.slice(0, 3);
      var tp = next.filter(function (u) { return hist[keyOf(u)]; })[0];
      if (tp && shown.indexOf(tp) < 0) shown.push(tp);
      if (!shown.length) { el.textContent = "No upcoming stops in the agency's predictions."; return; }
      var rows = shown.map(function (u) {
        var sched = u.arrDelay != null ? u.arrTime - u.arrDelay : null, b = sched ? bucket(sched) : null;
        var h = b && hist[keyOf(u)] && hist[keyOf(u)][b.key];
        var line = "<b>" + esc(stops[u.stopId] || "stop " + u.stopId) + "</b><br>" +
          "Agency predicts <b>" + hhmm(u.arrTime) + "</b>" + (sched ? ", timetable " + hhmm(sched) + " (" + lateText(u.arrDelay) + ")" : "");
        if (h) line += '<br><span class="m">Usually ' + lateText(h[0]) + " here on " + b.text + "; 1 trip in 7 is " + minutes(h[1]) + (h[1] > 0 ? " late or more" : " early or less") + " (" + h[2] + " trips).</span>";
        return line;
      });
      var age = trip.feedTimestamp ? Date.now() / 1000 - trip.feedTimestamp : 0;
      el.innerHTML = rows.join("<hr style='border:0;border-top:1px solid #dcdfe6;margin:6px 0'>") +
        '<div class="m" style="margin-top:6px">Predictions as of ' + hhmm(trip.feedTimestamp) + (age > 600 ? " — OUT OF DATE, ignore them" : "") +
        ". History is kept only for stops with a timetable time, and comes from past days only (" + ((res[1][1] || {}).first_day || "?") + " to " + ((res[1][1] || {}).last_day || "?") + "); our arrival times are estimates from GPS.</div>";
    }).catch(function (err) { el.textContent = "Couldn't load predictions (" + err.message + ")."; });
  }

  var map = new maplibregl.Map({ container: "livemap", style: STYLE, center: [-122.33, 47.6], zoom: 10.5, attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

  function toGeoJson(feed) {
    var now = Date.now() / 1000;
    return {
      type: "FeatureCollection",
      features: feed.vehicles.filter(function (v) {
        return v.lat != null && v.lon != null && (!filter || v.routeId === filter);
      }).map(function (v) {
        return { type: "Feature", geometry: { type: "Point", coordinates: [v.lon, v.lat] },
          properties: { route: v.routeId || "", color: colour(v.routeId), label: name(v.routeId), speed: v.speed, status: v.status,
            stopSeq: v.stopSeq, age: v.ts ? now - v.ts : null, ts: v.ts, trip: v.tripId || "", dir: v.directionId } };
      })
    };
  }

  function draw() {
    if (!last || !map.getSource("buses")) return;
    var data = toGeoJson(last);
    map.getSource("buses").setData(data);
    countEl.textContent = data.features.length + " buses shown";
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
      else setState("good", "Live: agency feed from " + seattle(last.timestamp));
      if (pick.length === 1) fillRoutePicker(last);
      draw();
    }).catch(function (e) {
      setState("bad", "Can't reach the live feed (" + e.message + "). Showing the last positions received" + (last ? " from " + seattle(last.timestamp) : "") + ".");
    });
  }

  map.on("load", function () {
    map.addSource("buses", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "buses", type: "circle", source: "buses", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3, 14, 8],
      "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.2 } });
    map.on("click", "buses", function (e) {
      var p = e.features[0].properties;
      var box = document.createElement("div");
      box.style.minWidth = "260px";
      box.innerHTML = headHtml(p) + '<div class="m" id="pred">Loading the agency\'s predictions…</div>';
      new maplibregl.Popup({ offset: 10, maxWidth: "360px" }).setLngLat(e.lngLat).setDOMContent(box).addTo(map);
      loadPredictions(p, box.querySelector("#pred"));
    });
    map.on("mouseenter", "buses", function () { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "buses", function () { map.getCanvas().style.cursor = ""; });
    refresh();
    timer = setInterval(function () { if (!document.hidden) refresh(); }, POLL_MS);
  });

  pick.addEventListener("change", function () { filter = pick.value; draw(); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && timer) refresh(); });

  // route numbers instead of internal ids; the page still works if this file is missing
  fetch("map/routes.json").then(function (r) { return r.ok ? r.json() : {}; }).then(function (j) {
    routes = j; if (last) { fillRoutePicker(last); draw(); }
  }).catch(function () {});
})();
