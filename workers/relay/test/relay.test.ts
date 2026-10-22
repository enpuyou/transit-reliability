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

import { parsePoint, trafficCacheSeconds, trafficSegment } from "../src/relay";

describe("parsePoint", () => {
  it("accepts a point over Seattle (rounded) and rejects the rest", () => {
    expect(parsePoint(new URLSearchParams("lat=47.61234&lon=-122.33456"))).toEqual({ lat: 47.6123, lon: -122.3346 });
    expect(parsePoint(new URLSearchParams("lat=40.7&lon=-74"))).toBeNull(); // New York
    expect(parsePoint(new URLSearchParams("lat=47.6"))).toBeNull(); // missing lon
    expect(parsePoint(new URLSearchParams("lat=abc&lon=-122.3"))).toBeNull();
    expect(parsePoint(new URLSearchParams("lat=&lon="))).toBeNull();
  });
});

describe("trafficSegment", () => {
  const p = { lat: 47.6123, lon: -122.3346 };
  const answer = { flowSegmentData: { frc: "FRC4", currentSpeed: 30, freeFlowSpeed: 50, confidence: 0.9, roadClosure: false, coordinates: { coordinate: [{ latitude: 47.6, longitude: -122.3 }, { latitude: 47.61, longitude: -122.31 }] } } };
  it("is off until the key is set", async () => {
    expect((await trafficSegment({ ...env }, p)).status).toBe(503);
  });
  it("sends the key in a header, returns a compact answer and sets our own cache rule", async () => {
    let seen = { url: "", key: "" };
    const fake = (async (url: string, init: RequestInit) => {
      seen = { url, key: (init.headers as Record<string, string>)["TomTom-Api-Key"] };
      return new Response(JSON.stringify(answer), { status: 200 });
    }) as unknown as typeof fetch;
    const res = await trafficSegment({ ...env, TOMTOM_KEY: "secret-value" }, p, fake);
    expect(seen.key).toBe("secret-value");
    expect(seen.url).not.toContain("secret-value");
    expect(seen.url).toContain("absolute/18/json?point=47.6123,-122.3346");
    expect(await res.json()).toEqual({ frc: "FRC4", cur: 30, free: 50, conf: 0.9, closed: false, coords: [[-122.3, 47.6], [-122.31, 47.61]] });
    expect(res.headers.get("cache-control")).toBe("public, max-age=90");
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
  });
  it("says 'none' when TomTom has no road near the point", async () => {
    const fake = (async () => new Response("too far", { status: 400 })) as unknown as typeof fetch;
    const res = await trafficSegment({ ...env, TOMTOM_KEY: "k" }, p, fake);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ none: true });
  });
  it("passes another upstream failure on without caching it", async () => {
    const fake = (async () => new Response("no", { status: 403 })) as unknown as typeof fetch;
    const res = await trafficSegment({ ...env, TOMTOM_KEY: "k" }, p, fake);
    expect(res.status).toBe(502);
    expect(res.headers.get("cache-control")).toBeNull();
  });
  it("keeps the cache time in a sane range", () => {
    expect(trafficCacheSeconds({ ...env, TRAFFIC_CACHE_SECONDS: "120" })).toBe(120);
    expect(trafficCacheSeconds({ ...env, TRAFFIC_CACHE_SECONDS: "1" })).toBe(90);
  });
});
