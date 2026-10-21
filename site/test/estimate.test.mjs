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

test("advance never moves the drawn bus backward, slows instead of freezing, and limits how fast it catches up", () => {
  assert.equal(E.advance(null, 500, 0.1, 10), 500, "first time: start at the estimate");
  assert.equal(E.advance(500, 510, 0.1, 10), 502.5, "estimate ahead: catching up is limited to 25 m/s");
  assert.equal(E.advance(500, 501, 5, 10), 501, "but never overshoots the estimate");
  assert.equal(E.advance(2000, 500, 0.1, 10), 500, "far ahead of the estimate: trust the estimate");
  // estimate fell behind by 20 m: the bus keeps going forward, a little slower than 7.5 m/s (75% of 10 m/s)
  assert.ok(Math.abs(E.advance(500, 480, 1, 10) - (500 + 7.5 * (1 - 20 / 400))) < 1e-9);
  assert.ok(E.advance(500, 480, 1, 10) > 500, "slows, does not freeze");
  assert.equal(E.advance(500, 100, 1, 10), 500, "400 m ahead of the estimate: stopped");
  assert.equal(E.advance(500, 480, 1, 0), 500, "a bus with no speed does not move");
  // over a stream of noisy estimates the drawn distance never decreases (unless it snaps back)
  let shown = null, last = -Infinity;
  for (const target of [100, 140, 120, 90, 200, 150, 160, 400, 380, 390]) {
    shown = E.advance(shown, target, 1, 8);
    assert.ok(shown >= last, `went backward: ${last} -> ${shown}`);
    last = shown;
  }
});

test("slice returns the stretch of line between two distances, turning corners", () => {
  const part = E.slice(line, 800, 1200);
  assert.ok(Math.abs(part[0][0] - east(800)) < 1e-6, "starts 800 m along");
  assert.deepEqual(part[1], line[2], "includes the corner it passes");
  assert.ok(Math.abs(part[part.length - 1][1] - north(200)) < 1e-6, "ends 1200 m along, on the northward stretch");
  assert.equal(E.slice(line, 100, 300).length, 2, "no corner in between: just the two ends");
});
