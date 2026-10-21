import { describe, expect, it } from "vitest";
import { cacheSeconds, feedFor, relay, type RelayEnv } from "../src/relay";

const env: RelayEnv = { KCM_BASE_URL: "https://example.test/feeds", CACHE_SECONDS: "15" };

describe("feedFor", () => {
  it("serves only the listed paths", () => {
    expect(feedFor("/vehicles")).toBe("vehiclepositions.pb");
    expect(feedFor("/alerts")).toBe("alerts.pb");
    expect(feedFor("/trips")).toBe("tripupdates.pb");
    expect(feedFor("/../secret")).toBeNull();
    expect(feedFor("/")).toBeNull();
  });
});

describe("cacheSeconds", () => {
  it("falls back to 15 for missing or silly values", () => {
    expect(cacheSeconds({ ...env, CACHE_SECONDS: "20" })).toBe(20);
    expect(cacheSeconds({ ...env, CACHE_SECONDS: "0" })).toBe(15);
    expect(cacheSeconds({ ...env, CACHE_SECONDS: "abc" })).toBe(15);
  });
});

describe("relay", () => {
  it("passes the bytes through with permission and cache headers", async () => {
    const fake = (async (url: string) => {
      expect(url).toBe("https://example.test/feeds/vehiclepositions.pb");
      return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
    }) as unknown as typeof fetch;
    const res = await relay(env, "vehiclepositions.pb", fake);
    expect(res.status).toBe(200);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    expect(res.headers.get("cache-control")).toBe("public, max-age=15");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
  });

  it("reports an upstream failure as 502 and does not mark it cacheable", async () => {
    const fake = (async () => new Response("no", { status: 503 })) as unknown as typeof fetch;
    const res = await relay(env, "vehiclepositions.pb", fake);
    expect(res.status).toBe(502);
    expect(res.headers.get("cache-control")).toBeNull();
  });
});

import { parseTile, trafficCacheSeconds, trafficTile } from "../src/relay";

describe("parseTile", () => {
  it("accepts a Seattle tile and rejects everything else", () => {
    // z12 tile over downtown Seattle (656/1430) and the z15 Capitol Hill tile used in testing
    expect(parseTile("/traffic/12/656/1430.png")).toEqual({ z: 12, x: 656, y: 1430 });
    expect(parseTile("/traffic/15/5250/11442.png")).toEqual({ z: 15, x: 5250, y: 11442 });
    expect(parseTile("/traffic/12/100/100.png")).toBeNull(); // not over Seattle
    expect(parseTile("/traffic/3/1/2.png")).toBeNull(); // zoom too low
    expect(parseTile("/traffic/18/1/2.png")).toBeNull(); // zoom too high
    expect(parseTile("/traffic/12/99999/1430.png")).toBeNull(); // outside the tile grid
    expect(parseTile("/traffic/12/656/1430.jpg")).toBeNull();
    expect(parseTile("/traffic/12/656/../1430.png")).toBeNull();
  });
});

describe("trafficTile", () => {
  const tile = { z: 12, x: 656, y: 1430 };
  it("is off until the key is set", async () => {
    const res = await trafficTile({ ...env }, tile);
    expect(res.status).toBe(503);
  });
  it("sends the key in a header, not in the web address, and sets our own cache rule", async () => {
    let seen = { url: "", key: "" };
    const fake = (async (url: string, init: RequestInit) => {
      seen = { url, key: (init.headers as Record<string, string>)["TomTom-Api-Key"] };
      return new Response(new Uint8Array([137, 80]), { status: 200, headers: { "cache-control": "no-store" } });
    }) as unknown as typeof fetch;
    const res = await trafficTile({ ...env, TOMTOM_KEY: "secret-value" }, tile, fake);
    expect(seen.key).toBe("secret-value");
    expect(seen.url).not.toContain("secret-value");
    expect(seen.url).toContain("/flow/relative0/12/656/1430.png?tileSize=512");
    expect(res.headers.get("cache-control")).toBe("public, max-age=90");
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
  });
  it("passes an upstream failure on without caching it", async () => {
    const fake = (async () => new Response("no", { status: 403 })) as unknown as typeof fetch;
    const res = await trafficTile({ ...env, TOMTOM_KEY: "k" }, tile, fake);
    expect(res.status).toBe(502);
    expect(res.headers.get("cache-control")).toBeNull();
  });
  it("keeps the cache time in a sane range", () => {
    expect(trafficCacheSeconds({ ...env, TRAFFIC_CACHE_SECONDS: "120" })).toBe(120);
    expect(trafficCacheSeconds({ ...env, TRAFFIC_CACHE_SECONDS: "1" })).toBe(90);
  });
});
