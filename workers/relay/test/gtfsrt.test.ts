import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { runningTrips } from "../src/gtfsrt";

// the saved sample and the values the pipeline's Python parser produced from it (site/test/vehicles_expected.json)
const bytes = new Uint8Array(readFileSync(new URL("../../../tests/fixtures/vehiclepositions.pb", import.meta.url)));
const expected = JSON.parse(readFileSync(new URL("../../../site/test/vehicles_expected.json", import.meta.url), "utf8"));

describe("runningTrips", () => {
  it("reads the same buses and trips as the Python parser", () => {
    const feed = runningTrips(bytes);
    expect(feed.timestamp).toBe(expected.header);
    const first = feed.trips[0];
    expect(first.tripId).toBe(expected.first[0].tripId);
    expect(first.routeId).toBe(expected.first[0].routeId);
    expect(first.vehicleId).toBe(expected.first[0].id);
    expect(Math.abs((first.lat as number) - expected.first[0].lat)).toBeLessThan(1e-4);
    expect(feed.trips.length).toBeLessThanOrEqual(expected.count);
    expect(feed.trips.length).toBeGreaterThan(expected.count * 0.9);
  });
});

import { finished } from "../src/finished";

describe("finished", () => {
  it("lists the trips that were running before and are not running now", () => {
    const before = { a: [null, "1", 1, 0, 0], b: [null, "2", 1, 0, 0], c: [null, "3", 1, 0, 0] } as never;
    const now = [{ tripId: "b" }, { tripId: "d" }] as never;
    expect(finished(before, now).sort()).toEqual(["a", "c"]);
    expect(finished({} as never, now)).toEqual([]);
  });
});
