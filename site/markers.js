/* The bus marker shapes, drawn white on a 64x64 canvas with the bus at the centre and the heading pointing up
   (north). The map colours them per route and rotates them (an "SDF" image); marker-options.html draws the same
   shapes to compare. Works in the browser only (needs a canvas). */
(function (root) {
  "use strict";
  var N = 64, C = N / 2;

  // "pointer": a round body with a separate small triangle just in front of it, like a location dot that points
  function pointer(g) {
    g.beginPath(); g.arc(C, C, 10, 0, 2 * Math.PI); g.fill();
    g.beginPath(); g.moveTo(C, C - 24); g.lineTo(C + 7, C - 14); g.lineTo(C - 7, C - 14); g.closePath(); g.fill();
  }
  // "chevron": a navigation arrowhead, no body
  function chevron(g) {
    g.beginPath(); g.moveTo(C, C - 17); g.lineTo(C + 13, C + 13); g.lineTo(C, C + 6); g.lineTo(C - 13, C + 13); g.closePath(); g.fill();
  }
  // "drop": a water-drop with a short tip
  function drop(g) {
    var r = 11, tip = C - 20, a = Math.acos(r / (C - tip));
    g.beginPath(); g.moveTo(C, tip); g.lineTo(C + r * Math.sin(a), C - r * Math.cos(a));
    g.arc(C, C, r, -Math.PI / 2 + a, -Math.PI / 2 - a + 2 * Math.PI, false); g.closePath(); g.fill();
  }
  // "beam": a wedge fanning out in front of the bus, drawn faint beside a plain disc (the map draws the disc)
  function beam(g) {
    g.beginPath(); g.moveTo(C, C); g.arc(C, C, 30, -Math.PI / 2 - 0.62, -Math.PI / 2 + 0.62, false); g.closePath(); g.fill();
  }
  // "badge": a plain disc with a small white arrow inside (the map draws the disc; this is the arrow)
  function badge(g) {
    g.beginPath(); g.moveTo(C, C - 11); g.lineTo(C + 8, C + 8); g.lineTo(C, C + 3); g.lineTo(C - 8, C + 8); g.closePath(); g.fill();
  }

  var DRAW = { pointer: pointer, chevron: chevron, badge: badge, beam: beam, drop: drop };

  /** ImageData for one marker kind. */
  function image(kind) {
    var c = document.createElement("canvas"); c.width = c.height = N;
    var g = c.getContext("2d"); g.fillStyle = "#ffffff"; DRAW[kind](g);
    return g.getImageData(0, 0, N, N);
  }

  root.Markers = { KINDS: Object.keys(DRAW), image: image, SIZE: N };
})(window);
