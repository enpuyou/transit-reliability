/* The shapes the map draws, white on a 64x64 canvas with the bus at the centre and the heading pointing up
   (north). The map colours them per route and rotates them (an "SDF" image). "beam" is the faint wedge in
   front of a bus; "chevron" is the arrowhead used along a highlighted route. Browser only (needs a canvas). */
(function (root) {
  "use strict";
  var N = 64, C = N / 2;

  // "chevron": a navigation arrowhead, no body
  function chevron(g) {
    g.beginPath(); g.moveTo(C, C - 17); g.lineTo(C + 13, C + 13); g.lineTo(C, C + 6); g.lineTo(C - 13, C + 13); g.closePath(); g.fill();
  }
  // "beam": a wedge fanning out in front of the bus, drawn faint beside a plain disc (the map draws the disc)
  function beam(g) {
    g.beginPath(); g.moveTo(C, C); g.arc(C, C, 30, -Math.PI / 2 - 0.62, -Math.PI / 2 + 0.62, false); g.closePath(); g.fill();
  }
  var DRAW = { chevron: chevron, beam: beam };

  /** ImageData for one marker kind. */
  function image(kind) {
    var c = document.createElement("canvas"); c.width = c.height = N;
    var g = c.getContext("2d"); g.fillStyle = "#ffffff"; DRAW[kind](g);
    return g.getImageData(0, 0, N, N);
  }

  root.Markers = { KINDS: Object.keys(DRAW), image: image, SIZE: N };
})(window);
