import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { bearingOnLine, compass } = require("../heading.js");
const { decodeVehicles } = require("../gtfsrt.js");

test("a line running east gives bearing 90, north 0, and far-away points give -1", () => {
  const east = [[[-122.3, 47.6], [-122.29, 47.6]]];
  assert.ok(Math.abs(bearingOnLine(east, -122.295, 47.6001) - 90) < 0.5);
  const north = [[[-122.3, 47.6], [-122.3, 47.61]]];
  assert.ok(Math.abs(bearingOnLine(north, -122.3, 47.605) - 0) < 0.5);
  assert.equal(bearingOnLine(east, -122.295, 47.7), -1);
  assert.equal(bearingOnLine([], -122.3, 47.6), -1);
});

test("compass words", () => {
  assert.equal(compass(0), "north");
  assert.equal(compass(92), "east");
  assert.equal(compass(225), "southwest");
  assert.equal(compass(-1), "");
});

test("heading read off the route line agrees with the heading the agency reports (saved sample)", () => {
  const shapes = JSON.parse(readFileSync(new URL("../map/shapes.json", import.meta.url), "utf8"));
  const feed = decodeVehicles(new Uint8Array(readFileSync(new URL("../../tests/fixtures/vehiclepositions.pb", import.meta.url))));
  let compared = 0, close = 0;
  for (const v of feed.vehicles) {
    if (v.bearing == null || v.lat == null) continue;
    const mine = bearingOnLine(shapes[v.routeId + "|" + v.directionId], v.lon, v.lat);
    if (mine < 0) continue;
    compared++;
    const diff = Math.abs(((mine - v.bearing + 540) % 360) - 180);
    if (diff <= 45) close++;
  }
  assert.ok(compared >= 5, `only ${compared} buses to compare`);
  // Most buses agree. The rest are mostly at terminals, waiting, or running out of service, where the
  // actual movement differs from the route line; the map prefers the agency's own heading when it has one.
  assert.ok(close / compared >= 0.65, `${close} of ${compared} within 45 degrees`);
});
