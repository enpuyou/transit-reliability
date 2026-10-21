export interface DispatcherEnv {
  GITHUB_TOKEN: string;
  GITHUB_REPO: string; // "owner/name"
}

export interface DispatchResult {
  event: "dispatch";
  workflow: string;
  ok: boolean;
  status: number; // 0 when the request never got an answer
  attempts: number;
  message?: string;
}

// cron expression (exactly as in wrangler.toml) -> workflow file to run
export const SCHEDULE: Record<string, string> = {
  "*/15 * * * *": "publish_site.yml", // live probes and the site, every 15 minutes
  "5 * * * *": "watchdog.yml", // alerting watchdog, hourly at :05
  "20 * * * *": "weather.yml", // National Weather Service observations into the landing volume, hourly at :20
};

export function workflowFor(cron: string): string | undefined {
  return SCHEDULE[cron];
}

export function buildRequest(env: DispatcherEnv, workflow: string): { url: string; init: RequestInit } {
  return {
    url: `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
    init: {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "transit-dispatcher",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  };
}

type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

/** Ask GitHub to run `workflow`. GitHub answers 204 with no body when it accepted the request.
 * A network error or a 5xx is retried once; 4xx (bad token, wrong file name) is not retryable. */
export async function dispatch(
  env: DispatcherEnv,
  workflow: string,
  fetchFn: FetchLike = fetch,
): Promise<DispatchResult> {
  const { url, init } = buildRequest(env, workflow);
  let status = 0;
  let message: string | undefined;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const response = await fetchFn(url, init);
      status = response.status;
      if (status === 204) return { event: "dispatch", workflow, ok: true, status, attempts: attempt };
      // GitHub's error body is JSON with a `message`; never echo anything else (no headers, no token)
      message = (await response.text()).slice(0, 200).replaceAll(env.GITHUB_TOKEN, "***");
      if (status < 500) break;
    } catch (error) {
      status = 0;
      message = String(error).slice(0, 200).replaceAll(env.GITHUB_TOKEN, "***");
    }
  }
  return { event: "dispatch", workflow, ok: false, status, attempts: status < 500 && status !== 0 ? 1 : 2, message };
}
