/* Which way is a bus facing? The agency's feed carries a heading for only a few buses, so we read it off the
   route line instead: the line for a route direction runs in the direction of travel, and a bus sits on it.
   Works in the browser (window.Heading) and in Node (module.exports). */
(function (root) {
  "use strict";

  /** Compass bearing in degrees (0 = north, 90 = east) of the part of `lines` nearest to the point, or -1.
      `lines` is a list of lines, each a list of [lon, lat]. `maxMetres` rejects buses far from the line. */
  function bearingOnLine(lines, lon, lat, maxMetres) {
    var kx = Math.cos(lat * Math.PI / 180), best = Infinity, bearing = -1;
    (lines || []).forEach(function (line) {
      for (var i = 0; i + 1 < line.length; i++) {
        var ax = (line[i][0] - lon) * kx, ay = line[i][1] - lat;
        var bx = (line[i + 1][0] - lon) * kx, by = line[i + 1][1] - lat;
        var dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
        if (len2 === 0) continue;
        var t = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2));
        var px = ax + t * dx, py = ay + t * dy, d2 = px * px + py * py;
        if (d2 < best) { best = d2; bearing = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360; }
      }
    });
    var metres = Math.sqrt(best) * 111320;
    return metres <= (maxMetres || 150) ? bearing : -1;
  }

  function compass(deg) {
    if (deg < 0) return "";
    return ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"][Math.round(deg / 45) % 8];
  }

  var api = { bearingOnLine: bearingOnLine, compass: compass };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Heading = api;
})(typeof window !== "undefined" ? window : globalThis);
