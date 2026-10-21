export interface RelayEnv {
  KCM_BASE_URL: string;
  CACHE_SECONDS: string;
  TRAFFIC_CACHE_SECONDS?: string;
  /** TomTom API key. A Worker secret (`wrangler secret put TOMTOM_KEY`), never in the browser or the repository. */
  TOMTOM_KEY?: string;
}

// Only these paths are served, so the relay cannot be used as an open proxy.
export const FEEDS: Record<string, string> = {
  "/vehicles": "vehiclepositions.pb",
  "/trips": "tripupdates.pb",
  "/alerts": "alerts.pb",
};

export const CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, OPTIONS",
};

export function feedFor(pathname: string): string | null {
  return FEEDS[pathname] ?? null;
}

export function cacheSeconds(env: RelayEnv): number {
  const n = Number(env.CACHE_SECONDS);
  return Number.isFinite(n) && n >= 5 && n <= 120 ? n : 15;
}

/** Fetch one agency feed and return it with permission and cache headers. */
export async function relay(env: RelayEnv, file: string, fetcher: typeof fetch = fetch): Promise<Response> {
  const upstream = await fetcher(`${env.KCM_BASE_URL}/${file}`);
  if (!upstream.ok) {
    return new Response(`upstream status ${upstream.status}`, { status: 502, headers: CORS_HEADERS });
  }
  const headers = new Headers(CORS_HEADERS);
  headers.set("content-type", "application/x-protobuf");
  headers.set("cache-control", `public, max-age=${cacheSeconds(env)}`);
  return new Response(upstream.body, { status: 200, headers });
}

// ---------- live traffic along a route: TomTom's per-road speed, fetched with our key and remembered here ----------
// The map asks for the traffic at points along the route that is selected (not a whole-region picture). For each point
// we ask TomTom's "flow segment data" for the road nearest it: the current speed, the speed with no traffic, and the
// road's shape. TomTom tells browsers not to cache; we keep each answer for TRAFFIC_CACHE_SECONDS and hand it out with
// our own cache rule, so the key stays here and many viewers share one request. Only points in the Seattle area are
// served, so the relay cannot be used to spend the allowance (2,500 requests a day on the free tier) elsewhere.

export const SEATTLE_BOX = { west: -122.7, east: -121.7, south: 47.2, north: 47.95 };

export interface Point {
  lat: number;
  lon: number;
}

/** The point named by ?lat=..&lon=.. (rounded to 4 decimals, about 11 m), or null if it is not over Seattle. */
export function parsePoint(search: URLSearchParams): Point | null {
  const lat = Number(search.get("lat")), lon = Number(search.get("lon"));
  if (!search.get("lat") || !search.get("lon") || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const p = { lat: Math.round(lat * 1e4) / 1e4, lon: Math.round(lon * 1e4) / 1e4 };
  const inside = p.lon > SEATTLE_BOX.west && p.lon < SEATTLE_BOX.east && p.lat > SEATTLE_BOX.south && p.lat < SEATTLE_BOX.north;
  return inside ? p : null;
}

export function trafficCacheSeconds(env: RelayEnv): number {
  const n = Number(env.TRAFFIC_CACHE_SECONDS);
  return Number.isFinite(n) && n >= 30 && n <= 600 ? n : 90;
}

export interface Segment {
  frc: string;
  cur: number; // km/h now
  free: number; // km/h with no traffic
  conf: number; // 0 to 1, how much TomTom trusts it
  closed: boolean;
  coords: [number, number][]; // [lon, lat] along the road
}

/** Ask TomTom for the road segment nearest a point; returns a compact JSON answer, or {"none":true} if there is none. */
export async function trafficSegment(env: RelayEnv, p: Point, fetcher: typeof fetch = fetch): Promise<Response> {
  if (!env.TOMTOM_KEY) return new Response("traffic is not configured", { status: 503, headers: CORS_HEADERS });
  const url = `https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?point=${p.lat},${p.lon}&unit=KMPH`;
  const upstream = await fetcher(url, { headers: { "TomTom-Api-Key": env.TOMTOM_KEY } });
  const headers = new Headers(CORS_HEADERS);
  headers.set("content-type", "application/json");
  if (upstream.status === 400) {
    // "point too far from the nearest road" or similar: nothing to show here; remember it for the same time
    headers.set("cache-control", `public, max-age=${trafficCacheSeconds(env)}`);
    return new Response(JSON.stringify({ none: true }), { status: 200, headers });
  }
  if (!upstream.ok) return new Response(`upstream status ${upstream.status}`, { status: 502, headers: CORS_HEADERS });
  const d = ((await upstream.json()) as { flowSegmentData?: Record<string, any> }).flowSegmentData;
  if (!d) return new Response(JSON.stringify({ none: true }), { status: 200, headers });
  const coords = ((d.coordinates?.coordinate ?? []) as { latitude: number; longitude: number }[]).map((c) => [c.longitude, c.latitude] as [number, number]);
  const seg: Segment = { frc: String(d.frc ?? ""), cur: Number(d.currentSpeed), free: Number(d.freeFlowSpeed), conf: Number(d.confidence ?? 0), closed: Boolean(d.roadClosure), coords };
  headers.set("cache-control", `public, max-age=${trafficCacheSeconds(env)}`);
  return new Response(JSON.stringify(seg), { status: 200, headers });
}
