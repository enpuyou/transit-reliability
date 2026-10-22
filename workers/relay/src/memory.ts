// Trip memory: remembers trips that have just finished, so the map can say "a bus is waiting at the start of the next
// trip" or "the previous trip never showed up" (decision 0025). The agency's files forget a finished trip within a
// couple of minutes, while a bus typically rests about 18 minutes before its next trip.
//
// One Durable Object (a small Worker with its own storage) wakes itself every 30 seconds with an alarm, reads the
// agency's bus file and compares the running trips with the previous wake-up: a trip that has disappeared has
// finished (or stopped reporting), and is written down with its bus and its last position. Finished trips are kept
// for 6 hours. Cost on Cloudflare's free plan (100,000 requests and 100,000 written rows a day for Durable Objects):
// about 2,900 wake-ups a day, and about 2,900 + 2,900 + 2 x 13,000 rows written (alarm, state, finished trips
// written and later deleted). It never touches the collector.
import { DurableObject } from "cloudflare:workers";
import { runningTrips } from "./gtfsrt";
import { finished, type State } from "./finished";

export interface MemoryEnv {
  KCM_BASE_URL: string;
}

export const WAKE_MS = 30_000;
const KEEP_S = 6 * 3600;

export class TripMemory extends DurableObject<MemoryEnv> {
  constructor(ctx: DurableObjectState, env: MemoryEnv) {
    super(ctx, env);
    ctx.storage.sql.exec(`CREATE TABLE IF NOT EXISTS ended (
      trip_id TEXT PRIMARY KEY, route_id TEXT, vehicle_id TEXT, ts INTEGER, lat REAL, lon REAL, ended_at INTEGER)`);
  }

  async alarm(): Promise<void> {
    try {
      const res = await fetch(`${this.env.KCM_BASE_URL}/vehiclepositions.pb`);
      if (!res.ok) throw new Error(`agency status ${res.status}`);
      const feed = runningTrips(new Uint8Array(await res.arrayBuffer()));
      const now = Math.floor(Date.now() / 1000);
      const state = (await this.ctx.storage.get<State>("state")) ?? { since: now, lastRun: 0, active: {} };
      for (const id of finished(state.active, feed.trips)) {
        const [route, bus, ts, lat, lon] = state.active[id];
        this.ctx.storage.sql.exec("INSERT OR REPLACE INTO ended VALUES (?, ?, ?, ?, ?, ?, ?)", id, route, bus, ts, lat, lon, now);
      }
      const active: State["active"] = {};
      for (const t of feed.trips) active[t.tripId] = [t.routeId, t.vehicleId, t.ts, t.lat, t.lon];
      // a long gap (memory asleep, agency outage) means we may have missed endings: restart the clock of what we know
      const since = state.lastRun && now - state.lastRun > 300 ? now : state.since;
      await this.ctx.storage.put<State>("state", { since, lastRun: now, active });
      if (now % 600 < 30) this.ctx.storage.sql.exec("DELETE FROM ended WHERE ended_at < ?", now - KEEP_S);
    } catch (err) {
      console.error(JSON.stringify({ event: "memory_wake_failed", error: String(err) }));
    } finally {
      await this.ctx.storage.setAlarm(Date.now() + WAKE_MS);
    }
  }

  /** GET /memory/ended?trips=a,b,c -> what we know about those trips; GET /memory/status -> health. */
  async fetch(request: Request): Promise<Response> {
    if ((await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(Date.now() + 1000);
    const url = new URL(request.url);
    const state = (await this.ctx.storage.get<State>("state")) ?? null;
    const headers = { "content-type": "application/json", "cache-control": "no-store" };
    if (url.pathname === "/memory/status") {
      const n = this.ctx.storage.sql.exec("SELECT count(*) AS n FROM ended").one().n;
      return new Response(JSON.stringify({ since: state?.since ?? null, lastRun: state?.lastRun ?? null, running: state ? Object.keys(state.active).length : 0, finishedKept: n }), { headers });
    }
    const ids = (url.searchParams.get("trips") ?? "").split(",").filter(Boolean).slice(0, 60);
    const ended: Record<string, { route: string | null; bus: string | null; ts: number | null; lat: number | null; lon: number | null; endedAt: number }> = {};
    if (ids.length) {
      const rows = this.ctx.storage.sql.exec(`SELECT * FROM ended WHERE trip_id IN (${ids.map(() => "?").join(",")})`, ...ids).toArray();
      for (const r of rows) ended[String(r.trip_id)] = { route: r.route_id as string | null, bus: r.vehicle_id as string | null, ts: r.ts as number | null, lat: r.lat as number | null, lon: r.lon as number | null, endedAt: r.ended_at as number };
    }
    const running = ids.filter((id) => state && id in state.active);
    return new Response(JSON.stringify({ since: state?.since ?? null, lastRun: state?.lastRun ?? null, now: Math.floor(Date.now() / 1000), ended, running }), { headers });
  }
}
