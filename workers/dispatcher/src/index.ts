import { dispatch, workflowFor, type DispatcherEnv } from "./dispatch";

export default {
  // The Worker is cron-only; answer stray HTTP hits instead of throwing.
  async fetch(): Promise<Response> {
    return new Response("transit-dispatcher: scheduled only", { status: 404 });
  },

  async scheduled(controller: ScheduledController, env: DispatcherEnv): Promise<void> {
    const workflow = workflowFor(controller.cron);
    if (!workflow) {
      console.error(JSON.stringify({ event: "unknown_cron", cron: controller.cron }));
      throw new Error(`no workflow configured for cron "${controller.cron}"`);
    }
    const result = await dispatch(env, workflow);
    // One structured line per run; view with `npm run tail`. The token is never logged.
    console.log(JSON.stringify({ ...result, cron: controller.cron, scheduledTime: controller.scheduledTime }));
    if (!result.ok) {
      // Throwing marks the invocation as failed in the Cloudflare dashboard.
      throw new Error(`dispatch of ${workflow} failed with status ${result.status}`);
    }
  },
} satisfies ExportedHandler<DispatcherEnv>;
