import type { Running } from "./gtfsrt";

export interface State {
  since: number; // when the memory started (unix s): before this we know nothing
  lastRun: number;
  active: Record<string, [string | null, string | null, number | null, number | null, number | null]>; // trip -> route, bus, ts, lat, lon
}

/** Trips in `before` that are not in `now`: they have finished (or stopped reporting). */
export function finished(before: State["active"], now: Running[]): string[] {
  const running = new Set(now.map((t) => t.tripId));
  return Object.keys(before).filter((id) => !running.has(id));
}

