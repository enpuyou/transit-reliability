import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const { decodeVehicles } = createRequire(import.meta.url)("../gtfsrt.js");
const bytes = readFileSync(new URL("../../tests/fixtures/vehiclepositions.pb", import.meta.url));
// expected values were produced by the pipeline's own Python parser (gtfs_rt/parse.py) on the same file
const expected = JSON.parse(readFileSync(new URL("./vehicles_expected.json", import.meta.url), "utf8"));

test("decodes the same buses as the Python parser", () => {
  const feed = decodeVehicles(new Uint8Array(bytes));
  assert.equal(feed.vehicles.length, expected.count);
  assert.equal(feed.timestamp, expected.header);
  const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-4, `${a} vs ${b}`);
  [...expected.first, expected.last].forEach((e, i) => {
    const v = i < expected.first.length ? feed.vehicles[i] : feed.vehicles[feed.vehicles.length - 1];
    assert.equal(v.id, e.id);
    assert.equal(v.tripId, e.tripId);
    assert.equal(v.routeId, e.routeId);
    assert.equal(v.stopSeq, e.stopSeq);
    close(v.lat, e.lat);
    close(v.lon, e.lon);
  });
});

test("finds a trip's predicted stop times, including negative delays", () => {
  const { findTripUpdate } = createRequire(import.meta.url)("../gtfsrt.js");
  const file = new Uint8Array(readFileSync(new URL("../../tests/fixtures/tripupdates.pb", import.meta.url)));
  const exp = JSON.parse(readFileSync(new URL("./tripupdates_expected.json", import.meta.url), "utf8"));
  assert.equal(findTripUpdate(file, "no-such-trip"), null);
  for (const t of exp.trips) {
    const got = findTripUpdate(file, t.tripId);
    assert.equal(got.feedTimestamp, exp.header);
    assert.deepEqual(
      got.updates.map((u) => [u.stopSeq, u.stopId, u.arrTime, u.arrDelay, u.depTime, u.depDelay]),
      // Python fills a missing prediction with zeros; the page reader says null
      t.updates.map(([seq, id, at, ad, dt, dd]) => [seq, id, at || null, at ? ad : null, dt || null, dt ? dd : null]),
    );
  }
});
