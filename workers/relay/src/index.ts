import { CORS_HEADERS, feedFor, parsePoint, relay, trafficSegment, type RelayEnv } from "./relay";

export default {
  async fetch(request: Request, env: RelayEnv, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
    if (request.method !== "GET") return new Response("method not allowed", { status: 405, headers: CORS_HEADERS });
    const url = new URL(request.url);
    const isSegment = url.pathname === "/traffic/segment";
    const point = isSegment ? parsePoint(url.searchParams) : null;
    if (isSegment && !point) return new Response("not a point over the Seattle area", { status: 404, headers: CORS_HEADERS });
    const file = isSegment ? null : feedFor(url.pathname);
    if (!isSegment && !file) return new Response("not found", { status: 404, headers: CORS_HEADERS });

    // The remembered copy is shared by every viewer; the agency is asked only on a miss.
    const cache = caches.default;
    const key = new Request(point ? `${url.origin}${url.pathname}?lat=${point.lat}&lon=${point.lon}` : `${url.origin}${url.pathname}`);
    const hit = await cache.match(key);
    if (hit) return hit;
    const fresh = point ? await trafficSegment(env, point) : await relay(env, file as string);
    if (fresh.status === 200) ctx.waitUntil(cache.put(key, fresh.clone()));
    return fresh;
  },
} satisfies ExportedHandler<RelayEnv>;
