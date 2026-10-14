import { afterEach, describe, expect, it, vi } from "vitest";
import { collect, FAST_INTERVAL_MS, remotePath, SOURCES, type CollectorEnv } from "../src/collect";

const noSleep = async (): Promise<void> => {};

const env: CollectorEnv = {
  KCM_BASE_URL: "https://feeds.example",
  LANDING_VOLUME_PATH: "/Volumes/workspace/transit_landing/raw",
  DATABRICKS_HOST: "https://dbx.example/",
  DATABRICKS_TOKEN: "secret",
};

afterEach(() => vi.unstubAllGlobals());

describe("remotePath", () => {
  it("uses the UTC date and epoch milliseconds", () => {
    const when = new Date("2026-10-13T23:59:00Z");
    expect(remotePath("/base", "alerts", when)).toBe(`/base/alerts/dt=2026-10-13/${when.getTime()}.pb`);
  });
});

describe("source schedule", () => {
  it("fetches alerts only every fifth minute", () => {
    const alerts = SOURCES.find((s) => s.name === "alerts")!;
    expect(alerts.due(new Date("2026-10-13T10:05:00Z"))).toBe(true);
    expect(alerts.due(new Date("2026-10-13T10:06:00Z"))).toBe(false);
  });
});

describe("collect", () => {
  it("uploads each due source and isolates failures", async () => {
    const uploads: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          expect((init.headers as Record<string, string>).Authorization).toBe("Bearer secret");
          uploads.push(url);
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("tripupdates.pb")) return new Response("nope", { status: 500 });
        return new Response(new Uint8Array([1, 2, 3]), {
          headers: { "content-type": "application/x-protobuf" },
        });
      }),
    );
    const when = new Date("2026-10-13T10:06:30Z");
    const results = await collect(env, when, noSleep, () => when.getTime());
    expect(results.filter((r) => r.source === "trip_updates").map((r) => r.ok)).toEqual([false]);
    expect(results.filter((r) => r.source === "vehicle_positions").every((r) => r.ok)).toBe(true);
    expect(uploads.every((u) => u.includes("/vehicle_positions/"))).toBe(true);
    expect(uploads[0]).toContain("https://dbx.example/api/2.0/fs/files/Volumes/");
    expect(uploads[0]).toContain("/vehicle_positions/dt=2026-10-13/");
  });

  it("rejects HTML error pages served with HTTP 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>", { headers: { "content-type": "text/html" } })),
    );
    const results = await collect(env, new Date("2026-10-13T10:06:00Z"), noSleep);
    expect(results.every((r) => !r.ok)).toBe(true);
  });

  it("fetches vehicle positions three times per run, 20 seconds apart", async () => {
    const sleeps: number[] = [];
    const fetched: string[] = [];
    let version = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "PUT") return new Response(null, { status: 204 });
        fetched.push(url);
        return new Response(new Uint8Array([1]), {
          headers: { "content-type": "application/x-protobuf", etag: `"v${version++}"` },
        });
      }),
    );
    const results = await collect(env, new Date("2026-10-13T10:06:00Z"), async (ms) => {
      sleeps.push(ms);
    });
    expect(sleeps).toEqual([FAST_INTERVAL_MS, FAST_INTERVAL_MS]);
    expect(fetched.filter((u) => u.endsWith("vehiclepositions.pb"))).toHaveLength(3);
    expect(fetched.filter((u) => u.endsWith("tripupdates.pb"))).toHaveLength(1);
    expect(results.filter((r) => r.source === "vehicle_positions" && r.path)).toHaveLength(3);
  });

  it("skips the upload when the feed version has not changed", async () => {
    let puts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          puts++;
          return new Response(null, { status: 204 });
        }
        return new Response(new Uint8Array([1]), {
          headers: { "content-type": "application/x-protobuf", etag: '"same"' },
        });
      }),
    );
    const results = await collect(env, new Date("2026-10-13T10:06:00Z"), noSleep);
    const vehicles = results.filter((r) => r.source === "vehicle_positions");
    expect(vehicles.map((r) => Boolean(r.skipped))).toEqual([false, true, true]);
    expect(puts).toBe(2); // one vehicle upload plus one trip-update upload
  });

  it("collects only the sources in SOURCE_SET", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "PUT"
          ? new Response(null, { status: 204 })
          : new Response(new Uint8Array([1]), {
              headers: { "content-type": "application/x-protobuf", etag: `"${Math.random()}"` },
            }),
      ),
    );
    const when = new Date("2026-10-13T10:05:00Z"); // alerts due
    const trips = await collect({ ...env, SOURCE_SET: "trips" }, when, noSleep);
    expect([...new Set(trips.map((r) => r.source))].sort()).toEqual(["alerts", "trip_updates"]);
    const vehicles = await collect({ ...env, SOURCE_SET: "vehicles" }, when, noSleep);
    expect([...new Set(vehicles.map((r) => r.source))]).toEqual(["vehicle_positions"]);
  });
});
