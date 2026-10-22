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

  /** The coordinates of the line between distances d1 and d2 (d1 <= d2), for drawing the movement we estimated. */
  function slice(line, d1, d2) {
    var kx = scale(line), acc = 0, out = [pointAt(line, d1).slice(0, 2)];
    for (var i = 0; i + 1 < line.length; i++) {
      acc += Math.hypot((line[i + 1][0] - line[i][0]) * kx, (line[i + 1][1] - line[i][1]) * M_PER_DEG_LAT);
      if (acc > d1 && acc < d2) out.push([line[i + 1][0], line[i + 1][1]]);
    }
    out.push(pointAt(line, d2).slice(0, 2));
    return out;
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
  var AHEAD_SCALE = 400;   // m: how far ahead of the estimate the drawn bus has slowed to a stop

  /** The distance at which to draw the bus after `dtSec` more seconds. It never moves backward. When the estimate
      has fallen behind the drawn bus (a new report showed the bus slower than guessed) the bus does not freeze: it
      slows in proportion to how far ahead of the estimate it is, so the estimate catches up smoothly (replayed on
      120 real trips: standing still 6% of the time while really moving, against 33% for freezing, at the same
      accuracy). When the estimate is ahead of the drawn bus it catches up, but no faster than CATCH_UP. */
  function advance(shown, target, dtSec, vel) {
    var dt = Math.max(0, dtSec);
    if (shown == null) return target;
    if (shown - target > SNAP_BACK) return target;
    if (target > shown) return shown + Math.min(target - shown, CATCH_UP * dt);
    return shown + Math.max(0, FACTOR * (vel || 0) * (1 - (shown - target) / AHEAD_SCALE)) * dt;
  }

  /* ---------- timetable pace ---------- */
  var MOVING = 3; // m/s: at its last report the bus was moving if faster than this

  function interp(xs, ys, x) {
    if (x <= xs[0]) return ys[0];
    var n = xs.length;
    if (x >= xs[n - 1]) return ys[n - 1];
    for (var i = 0; i + 1 < n; i++) {
      if (x < xs[i + 1]) return xs[i + 1] > xs[i] ? ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]) : ys[i];
    }
    return ys[n - 1];
  }

  /** A trip's timetable along a line: stops as [{dist (m along the line), time (unix s)}] in trip order, kept only
      where both distance and time increase (so it can be read both ways). */
  function timetable(stops) {
    var d = [], t = [];
    stops.forEach(function (s) {
      if (!d.length || (s.dist > d[d.length - 1] && s.time >= t[t.length - 1])) { d.push(s.dist); t.push(s.time); }
    });
    return d.length >= 2 ? { d: d, t: t } : null;
  }

  /** "Timetable pace": keep the bus exactly as late as it was at its last report, and move it at the timetable's
      pace (which knows the slow stretches and the timetable stops), never behind its last report. */
  function timetablePace(rep, tt, nowSec) {
    var delay = rep.ts - interp(tt.d, tt.t, rep.dist);
    return Math.max(rep.dist, interp(tt.t, tt.d, nowSec - delay));
  }

  /** The estimate the map draws ("mixed, halfway"): while the bus was moving at its last report, halfway between
      timetable pace and recent speed; when it was slow or stopped (or there is no timetable), recent speed alone.
      Replayed on 240 real trips: typical error 120 m against 131 m for recent speed alone and 253 m for the raw
      report, and no more often much worse than the report (scripts/lag_estimators.py). */
  function mixed(rep, vel, nowSec, lineLength, tt) {
    var bySpeed = estimate(rep, vel, nowSec, lineLength);
    if (!tt || vel == null || vel <= MOVING) return bySpeed;
    return Math.min(lineLength, (timetablePace(rep, tt, nowSec) + bySpeed) / 2);
  }

  var api = { mixed: mixed, timetable: timetable, timetablePace: timetablePace, advance: advance, slice: slice, project: project, length: length, pointAt: pointAt, speed: speed, estimate: estimate, FACTOR: FACTOR };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Estimate = api;
})(typeof window !== "undefined" ? window : globalThis);
