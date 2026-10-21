import { CORS_HEADERS, feedFor, parseTile, relay, trafficTile, type RelayEnv } from "./relay";

export default {
  async fetch(request: Request, env: RelayEnv, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
    if (request.method !== "GET") return new Response("method not allowed", { status: 405, headers: CORS_HEADERS });
    const url = new URL(request.url);
    const tile = parseTile(url.pathname);
    const file = tile ? null : feedFor(url.pathname);
    if (!tile && !file) return new Response("not found", { status: 404, headers: CORS_HEADERS });

    // The remembered copy is shared by every viewer; the agency is asked only on a miss.
    const cache = caches.default;
    const key = new Request(`${url.origin}${url.pathname}`);
    const hit = await cache.match(key);
    if (hit) return hit;
    const fresh = tile ? await trafficTile(env, tile) : await relay(env, file as string);
    if (fresh.status === 200) ctx.waitUntil(cache.put(key, fresh.clone()));
    return fresh;
  },
} satisfies ExportedHandler<RelayEnv>;
