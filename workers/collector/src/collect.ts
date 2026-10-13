export interface CollectorEnv {
  KCM_BASE_URL: string;
  LANDING_VOLUME_PATH: string;
  /** Which sources this Worker collects; separate Workers get separate CPU budgets. */
  SOURCE_SET?: "all" | "vehicles" | "trips";
  DATABRICKS_HOST: string;
  DATABRICKS_TOKEN: string;
}

export interface Source {
  /** Directory name under the landing volume; must match the bronze source names. */
  name: "vehicle_positions" | "trip_updates" | "alerts";
  file: string;
  group: "vehicles" | "trips";
  /** Fetch only when this returns true for the scheduled time. */
  due: (when: Date) => boolean;
  /** Extra fetches after the first, one every FAST_INTERVAL_MS, within the same run. */
  repeats: number;
}

/**
 * KCM publishes a new feed version about every 21 seconds. A cron run is one per minute,
 * so fast sources are fetched again inside the run, waiting between fetches. Waiting is
 * wall time, not CPU time, so it does not count against the Free-plan CPU limit.
 */
export const FAST_INTERVAL_MS = 20_000;

export const SOURCES: Source[] = [
  { name: "vehicle_positions", group: "vehicles", file: "vehiclepositions.pb", due: () => true, repeats: 2 },
  { name: "trip_updates", group: "trips", file: "tripupdates.pb", due: () => true, repeats: 0 },
  { name: "alerts", group: "trips", file: "alerts.pb", due: (when) => when.getUTCMinutes() % 5 === 0, repeats: 0 },
];

export interface Result {
  source: string;
  ok: boolean;
  bytes?: number;
  path?: string;
  /** True when the feed had not changed since the previous fetch in this run. */
  skipped?: boolean;
  error?: string;
}

/** Build the immutable per-snapshot volume path (UTC date, epoch-ms file name). */
export function remotePath(base: string, source: string, when: Date): string {
  const date = when.toISOString().slice(0, 10);
  return `${base}/${source}/dt=${date}/${when.getTime()}.pb`;
}

async function withRetry<T>(attempt: () => Promise<T>): Promise<T> {
  try {
    return await attempt();
  } catch {
    return await attempt();
  }
}

async function fetchFeed(url: string): Promise<{ body: ArrayBuffer; etag: string | null }> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`feed HTTP ${response.status}`);
  }
  const body = await response.arrayBuffer();
  const type = response.headers.get("content-type") ?? "";
  if (body.byteLength === 0 || type.includes("text/html")) {
    throw new Error(`feed returned ${body.byteLength} bytes (${type || "no content-type"})`);
  }
  return { body, etag: response.headers.get("etag") };
}

async function upload(env: CollectorEnv, path: string, body: ArrayBuffer): Promise<void> {
  const host = env.DATABRICKS_HOST.replace(/\/$/, "");
  const response = await fetch(`${host}/api/2.0/fs/files${path}?overwrite=true`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${env.DATABRICKS_TOKEN}`,
      "Content-Type": "application/octet-stream",
    },
    body,
  });
  if (!response.ok) {
    throw new Error(`upload HTTP ${response.status}`);
  }
}

const realSleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Collect every due source. Fast sources are fetched again every FAST_INTERVAL_MS inside
 * the run. One source failing never stops the others. Files are named by actual fetch time.
 */
export async function collect(
  env: CollectorEnv,
  when: Date,
  sleep: (ms: number) => Promise<void> = realSleep,
  now: () => number = Date.now,
): Promise<Result[]> {
  const scheduled = new Date(Math.floor(when.getTime() / 60000) * 60000);
  const lastEtag = new Map<string, string | null>();

  const pull = async (source: Source): Promise<Result> => {
    try {
      const { body, etag } = await withRetry(() => fetchFeed(`${env.KCM_BASE_URL}/${source.file}`));
      if (etag !== null && lastEtag.get(source.name) === etag) {
        return { source: source.name, ok: true, skipped: true };
      }
      lastEtag.set(source.name, etag);
      const path = remotePath(env.LANDING_VOLUME_PATH, source.name, new Date(now()));
      await withRetry(() => upload(env, path, body));
      return { source: source.name, ok: true, bytes: body.byteLength, path };
    } catch (error) {
      return { source: source.name, ok: false, error: String(error) };
    }
  };

  const set = env.SOURCE_SET ?? "all";
  const due = SOURCES.filter(
    (source) => (set === "all" || source.group === set) && source.due(scheduled),
  );
  const results = await Promise.all(due.map(pull));
  const rounds = Math.max(0, ...due.map((source) => source.repeats));
  for (let round = 1; round <= rounds; round++) {
    await sleep(FAST_INTERVAL_MS);
    const fast = due.filter((source) => source.repeats >= round);
    results.push(...(await Promise.all(fast.map(pull))));
  }
  return results;
}
