import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const E = createRequire(import.meta.url)("../estimate.js");
// a line running due east for about 1,000 m at Seattle's latitude (two segments), then north 500 m
const east = (m) => -122.33 + m / (111320 * Math.cos(47.6 * Math.PI / 180));
const north = (m) => 47.6 + m / 110540;
const line = [[east(0), 47.6], [east(500), 47.6], [east(1000), 47.6], [east(1000), north(500)]];

test("project and pointAt agree, and the line length is right", () => {
  assert.ok(Math.abs(E.length(line) - 1500) < 2);
  const p = E.project(line, east(700), north(3)); // 3 m north of the line, 700 m along
  assert.ok(Math.abs(p.dist - 700) < 1 && Math.abs(p.off - 3) < 0.5);
  const at = E.pointAt(line, 700);
  assert.ok(Math.abs(at[0] - east(700)) < 1e-6 && Math.abs(at[1] - 47.6) < 1e-6);
  assert.ok(Math.abs(at[2] - 90) < 0.5, "heading east");
  assert.ok(Math.abs(E.pointAt(line, 1200)[2] - 0) < 0.5, "heading north on the last stretch");
  assert.deepEqual(E.pointAt(line, 99999).slice(0, 2), [line[3][0], line[3][1]], "clamped to the end");
});

test("speed uses reports 15 to 60 s apart and is clamped", () => {
  assert.equal(E.speed([{ ts: 0, dist: 0 }]), null);
  assert.equal(E.speed([{ ts: 0, dist: 0 }, { ts: 10, dist: 100 }]), null, "too close together to trust");
  assert.equal(E.speed([{ ts: 0, dist: 0 }, { ts: 90, dist: 900 }]), null, "too old to trust");
  assert.ok(Math.abs(E.speed([{ ts: 0, dist: 0 }, { ts: 20, dist: 200 }]) - 10) < 1e-9);
  assert.equal(E.speed([{ ts: 0, dist: 500 }, { ts: 20, dist: 100 }]), 0, "never backwards");
  assert.equal(E.speed([{ ts: 0, dist: 0 }, { ts: 20, dist: 99999 }]), 24.4, "clamped");
  // skips a report less than 15 s before the newest and uses the next one back: (600 - 0) / 20 = 30 m/s, clamped
  assert.equal(E.speed([{ ts: 0, dist: 0 }, { ts: 10, dist: 500 }, { ts: 20, dist: 600 }]), 24.4);
  assert.ok(Math.abs(E.speed([{ ts: 0, dist: 0 }, { ts: 10, dist: 50 }, { ts: 20, dist: 120 }]) - 6) < 1e-9);
});

test("estimate moves a share of the recent speed forward, capped by age and by the line end", () => {
  const rep = { ts: 1000, dist: 100 };
  assert.equal(E.estimate(rep, 10, 1000, 1500), 100, "no age, no movement");
  assert.ok(Math.abs(E.estimate(rep, 10, 1060, 1500) - (100 + 0.75 * 10 * 60)) < 1e-9);
  assert.ok(Math.abs(E.estimate(rep, 10, 5000, 1500) - (100 + 0.75 * 10 * 120)) < 1e-9, "age capped at 120 s");
  assert.equal(E.estimate({ ts: 0, dist: 1490 }, 10, 100, 1500), 1500, "never past the end of the line");
  assert.equal(E.estimate(rep, 10, 900, 1500), 100, "a report from the future does not move the bus back");
});
