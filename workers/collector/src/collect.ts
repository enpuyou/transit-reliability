export interface CollectorEnv {
  KCM_BASE_URL: string;
  LANDING_VOLUME_PATH: string;
  DATABRICKS_HOST: string;
  DATABRICKS_TOKEN: string;
}

export interface Source {
  /** Directory name under the landing volume; must match the bronze source names. */
  name: "vehicle_positions" | "trip_updates" | "alerts";
  file: string;
  /** Fetch only when this returns true for the scheduled time. */
  due: (when: Date) => boolean;
}

export const SOURCES: Source[] = [
  { name: "vehicle_positions", file: "vehiclepositions.pb", due: () => true },
  { name: "trip_updates", file: "tripupdates.pb", due: () => true },
  { name: "alerts", file: "alerts.pb", due: (when) => when.getUTCMinutes() % 5 === 0 },
];

export interface Result {
  source: string;
  ok: boolean;
  bytes?: number;
  path?: string;
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

async function fetchFeed(url: string): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`feed HTTP ${response.status}`);
  }
  const body = await response.arrayBuffer();
  const type = response.headers.get("content-type") ?? "";
  if (body.byteLength === 0 || type.includes("text/html")) {
    throw new Error(`feed returned ${body.byteLength} bytes (${type || "no content-type"})`);
  }
  return body;
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

/** Collect every due source. One source failing never stops the others. */
export async function collect(env: CollectorEnv, when: Date): Promise<Result[]> {
  // Use a single timestamp for the whole run, truncated to the scheduled minute.
  const scheduled = new Date(Math.floor(when.getTime() / 60000) * 60000);
  const due = SOURCES.filter((source) => source.due(scheduled));
  return Promise.all(
    due.map(async (source): Promise<Result> => {
      try {
        const body = await withRetry(() => fetchFeed(`${env.KCM_BASE_URL}/${source.file}`));
        const path = remotePath(env.LANDING_VOLUME_PATH, source.name, scheduled);
        await withRetry(() => upload(env, path, body));
        return { source: source.name, ok: true, bytes: body.byteLength, path };
      } catch (error) {
        return { source: source.name, ok: false, error: String(error) };
      }
    }),
  );
}
