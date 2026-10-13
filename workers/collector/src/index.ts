import { collect, type CollectorEnv } from "./collect";

export default {
  // The Worker is cron-only; answer stray HTTP hits instead of throwing.
  async fetch(): Promise<Response> {
    return new Response("transit-collector: scheduled only", { status: 404 });
  },

  async scheduled(controller: ScheduledController, env: CollectorEnv): Promise<void> {
    const results = await collect(env, new Date(controller.scheduledTime));
    for (const result of results) {
      // Structured one-line logs; view with `npm run tail`. Never log the token.
      console.log(JSON.stringify(result));
    }
    if (results.some((result) => !result.ok)) {
      console.error(JSON.stringify({ event: "collect_failed", failed: results.filter((r) => !r.ok) }));
    }
  },
} satisfies ExportedHandler<CollectorEnv>;
