/* Live bus map. Every POLL_MS the browser asks the relay Worker for the agency's vehicle file, decodes it
   (gtfsrt.js) and moves the dots. No Databricks involved: the page only talks to the relay and the map tiles. */
(function () {
  "use strict";
  // Where the relay Worker lives. Override for local testing with  map.html?relay=http://localhost:8787
  var RELAY = new URLSearchParams(location.search).get("relay") || "https://transit-relay.REPLACE_ME.workers.dev";
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
            stopSeq: v.stopSeq, age: v.ts ? now - v.ts : null, ts: v.ts, trip: v.tripId || "" } };
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
      var p = e.features[0].properties, bits = [];
      bits.push("<b>Route " + esc(p.label) + "</b>");
      if (p.ts) bits.push('<span class="m">last report ' + seattle(Number(p.ts)) + " (" + ago(Math.max(0, Date.now() / 1000 - Number(p.ts))) + " ago)</span>");
      if (p.speed != null && p.speed !== "null") bits.push("speed " + Math.round(Number(p.speed) * 2.237) + " mph");
      if (p.stopSeq != null && p.stopSeq !== "null") bits.push('<span class="m">heading to stop #' + p.stopSeq + " of its trip</span>");
      new maplibregl.Popup({ offset: 10 }).setLngLat(e.lngLat).setHTML(bits.join("<br>")).addTo(map);
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
