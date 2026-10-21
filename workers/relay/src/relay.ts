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

// ---------- live traffic: TomTom flow tiles, fetched with our key and remembered here ----------
// TomTom tells browsers not to cache its tiles (every map view would cost requests against the free allowance
// and the key would sit in the page). We fetch each tile once, keep it for TRAFFIC_CACHE_SECONDS and hand it out
// with our own cache rule. Only tiles over the Seattle area and zoom 9 to 17 are served, so the relay cannot be
// used to spend the allowance on the rest of the world.

export const TRAFFIC_ZOOM = { min: 9, max: 17 };
export const SEATTLE_BOX = { west: -122.7, east: -121.7, south: 47.2, north: 47.95 };
const TILE_PATH = /^\/traffic\/(\d{1,2})\/(\d{1,6})\/(\d{1,6})\.png$/;

export interface Tile {
  z: number;
  x: number;
  y: number;
}

function tileWest(x: number, z: number): number {
  return (x / 2 ** z) * 360 - 180;
}
function tileLat(y: number, z: number): number {
  const n = Math.PI - (2 * Math.PI * y) / 2 ** z;
  return (180 / Math.PI) * Math.atan(Math.sinh(n));
}

/** The tile named by a request path, or null if it is not a valid tile over the Seattle area. */
export function parseTile(pathname: string): Tile | null {
  const m = TILE_PATH.exec(pathname);
  if (!m) return null;
  const [z, x, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (z < TRAFFIC_ZOOM.min || z > TRAFFIC_ZOOM.max || x >= 2 ** z || y >= 2 ** z) return null;
  const west = tileWest(x, z), east = tileWest(x + 1, z), north = tileLat(y, z), south = tileLat(y + 1, z);
  const overlaps = east > SEATTLE_BOX.west && west < SEATTLE_BOX.east && north > SEATTLE_BOX.south && south < SEATTLE_BOX.north;
  return overlaps ? { z, x, y } : null;
}

export function trafficCacheSeconds(env: RelayEnv): number {
  const n = Number(env.TRAFFIC_CACHE_SECONDS);
  return Number.isFinite(n) && n >= 30 && n <= 600 ? n : 90;
}

/** Fetch one TomTom traffic flow tile (512 px, "relative0" colours) and return it with our cache headers. */
export async function trafficTile(env: RelayEnv, tile: Tile, fetcher: typeof fetch = fetch): Promise<Response> {
  if (!env.TOMTOM_KEY) return new Response("traffic is not configured", { status: 503, headers: CORS_HEADERS });
  const url = `https://api.tomtom.com/traffic/map/4/tile/flow/relative0/${tile.z}/${tile.x}/${tile.y}.png?tileSize=512`;
  const upstream = await fetcher(url, { headers: { "TomTom-Api-Key": env.TOMTOM_KEY } });
  if (!upstream.ok) return new Response(`upstream status ${upstream.status}`, { status: 502, headers: CORS_HEADERS });
  const headers = new Headers(CORS_HEADERS);
  headers.set("content-type", "image/png");
  headers.set("cache-control", `public, max-age=${trafficCacheSeconds(env)}`);
  return new Response(upstream.body, { status: 200, headers });
}
