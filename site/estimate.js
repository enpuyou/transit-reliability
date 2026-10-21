/* Where is a bus NOW, given a report that is already about a minute old? Move it forward along its route line
   at a share of its recent speed (tested on 250 real trips: this halves the error compared with leaving it at its
   last report; scripts/lag_experiment.py). All distances are metres along the route line. Works in the browser
   (window.Estimate) and in Node (module.exports). */
(function (root) {
  "use strict";
  var M_PER_DEG_LAT = 110540, M_PER_DEG_LON_AT_EQUATOR = 111320;
  var FACTOR = 0.75;       // share of the recent speed to apply: full speed overshoots (buses stop), 75% was best
  var MAX_AGE_S = 120;     // never move a bus forward for more than this long
  var MAX_SPEED = 24.4;    // m/s, 80 ft/s: faster than any bus, so a bad pair of fixes cannot fling it away
  var MIN_BASIS_S = 15, MAX_BASIS_S = 60; // recent speed comes from reports this far apart

  function scale(line) { return M_PER_DEG_LON_AT_EQUATOR * Math.cos(line[0][1] * Math.PI / 180); }

  /** Where a point sits along a line: { dist: metres from the start, off: metres from the line }. */
  function project(line, lon, lat) {
    var kx = scale(line), best = { dist: 0, off: Infinity }, acc = 0;
    for (var i = 0; i + 1 < line.length; i++) {
      var ax = (line[i][0] - lon) * kx, ay = (line[i][1] - lat) * M_PER_DEG_LAT;
      var bx = (line[i + 1][0] - lon) * kx, by = (line[i + 1][1] - lat) * M_PER_DEG_LAT;
      var dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy, len = Math.sqrt(len2);
      var t = len2 === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2));
      var px = ax + t * dx, py = ay + t * dy, off = Math.sqrt(px * px + py * py);
      if (off < best.off) best = { dist: acc + t * len, off: off };
      acc += len;
    }
    return best;
  }

  function length(line) {
    var kx = scale(line), acc = 0;
    for (var i = 0; i + 1 < line.length; i++) {
      acc += Math.hypot((line[i + 1][0] - line[i][0]) * kx, (line[i + 1][1] - line[i][1]) * M_PER_DEG_LAT);
    }
    return acc;
  }

  /** The point `dist` metres along the line (clamped to its ends): [lon, lat, compass bearing]. */
  function pointAt(line, dist) {
    var kx = scale(line), acc = 0, d = Math.max(0, dist);
    for (var i = 0; i + 1 < line.length; i++) {
      var dx = (line[i + 1][0] - line[i][0]) * kx, dy = (line[i + 1][1] - line[i][1]) * M_PER_DEG_LAT, len = Math.hypot(dx, dy);
      if (len > 0 && (d <= acc + len || i + 2 === line.length)) {
        var t = Math.min(1, (d - acc) / len);
        return [line[i][0] + (line[i + 1][0] - line[i][0]) * t, line[i][1] + (line[i + 1][1] - line[i][1]) * t,
          (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360];
      }
      acc += len;
    }
    var last = line[line.length - 1];
    return [last[0], last[1], 0];
  }

  /** Recent speed in m/s from reports [{ts, dist}] oldest to newest, or null if there is not enough to go on. */
  function speed(hist) {
    if (!hist || hist.length < 2) return null;
    var newest = hist[hist.length - 1];
    for (var j = hist.length - 2; j >= 0; j--) {
      var dt = newest.ts - hist[j].ts;
      if (dt >= MIN_BASIS_S) {
        if (dt > MAX_BASIS_S) return null;
        return Math.max(0, Math.min(MAX_SPEED, (newest.dist - hist[j].dist) / dt));
      }
    }
    return null;
  }

  /** Estimated distance along the line at wall-clock time `nowSec` (unix seconds). */
  function estimate(rep, vel, nowSec, lineLength) {
    var age = Math.max(0, Math.min(MAX_AGE_S, nowSec - rep.ts));
    return Math.min(lineLength, rep.dist + FACTOR * vel * age);
  }

  var CATCH_UP = 25;       // m/s: how fast a drawn bus may catch up to a higher estimate (a bus's top speed, about 56 mph)
  var SNAP_BACK = 800;     // m: if the drawn bus is this far AHEAD of the estimate, trust the estimate (something changed)

  /** The distance at which to draw the bus after `dtSec` more seconds: it never moves backward (it waits for the
      estimate to catch up, which is calmer than easing it back), and it catches up no faster than CATCH_UP. */
  function advance(shown, target, dtSec) {
    if (shown == null) return target;
    if (shown - target > SNAP_BACK) return target;
    if (target <= shown) return shown;
    return shown + Math.min(target - shown, CATCH_UP * Math.max(0, dtSec));
  }

  var api = { advance: advance, project: project, length: length, pointAt: pointAt, speed: speed, estimate: estimate, FACTOR: FACTOR };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Estimate = api;
})(typeof window !== "undefined" ? window : globalThis);
