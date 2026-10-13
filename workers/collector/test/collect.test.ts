import { afterEach, describe, expect, it, vi } from "vitest";
import { collect, remotePath, SOURCES, type CollectorEnv } from "../src/collect";

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
    const results = await collect(env, new Date("2026-10-13T10:06:30Z"));
    expect(results.map((r) => [r.source, r.ok])).toEqual([
      ["vehicle_positions", true],
      ["trip_updates", false],
    ]);
    expect(uploads).toHaveLength(1);
    expect(uploads[0]).toContain("https://dbx.example/api/2.0/fs/files/Volumes/");
    expect(uploads[0]).toContain("/vehicle_positions/dt=2026-10-13/");
  });

  it("rejects HTML error pages served with HTTP 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>", { headers: { "content-type": "text/html" } })),
    );
    const results = await collect(env, new Date("2026-10-13T10:06:00Z"));
    expect(results.every((r) => !r.ok)).toBe(true);
  });
});
