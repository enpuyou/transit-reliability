import { describe, expect, it } from "vitest";
import { cacheSeconds, feedFor, relay, type RelayEnv } from "../src/relay";

const env: RelayEnv = { KCM_BASE_URL: "https://example.test/feeds", CACHE_SECONDS: "15" };

describe("feedFor", () => {
  it("serves only the listed paths", () => {
    expect(feedFor("/vehicles")).toBe("vehiclepositions.pb");
    expect(feedFor("/alerts")).toBe("alerts.pb");
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
