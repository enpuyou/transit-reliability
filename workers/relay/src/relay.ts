export interface RelayEnv {
  KCM_BASE_URL: string;
  CACHE_SECONDS: string;
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
