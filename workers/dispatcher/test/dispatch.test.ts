import { describe, expect, it } from "vitest";
import { SCHEDULE, buildRequest, dispatch, workflowFor, type DispatcherEnv } from "../src/dispatch";

const env: DispatcherEnv = { GITHUB_TOKEN: "ghp_secret123", GITHUB_REPO: "owner/repo" };
// a 204 response must not carry a body (not even an empty string)
const respond = (status: number, body = "") => new Response(status === 204 ? null : body, { status });
const reply = (status: number, body = "") => async () => respond(status, body);

describe("schedule", () => {
  it("maps each cron to a workflow file and ignores unknown crons", () => {
    expect(workflowFor("*/15 * * * *")).toBe("publish_site.yml");
    expect(workflowFor("5 * * * *")).toBe("watchdog.yml");
    expect(workflowFor("20 * * * *")).toBe("weather.yml");
    expect(workflowFor("* * * * *")).toBeUndefined();
    expect(Object.keys(SCHEDULE)).toHaveLength(3);
  });
});

describe("buildRequest", () => {
  it("POSTs a workflow_dispatch for main, with the token only in the Authorization header", () => {
    const { url, init } = buildRequest(env, "publish_site.yml");
    expect(url).toBe("https://api.github.com/repos/owner/repo/actions/workflows/publish_site.yml/dispatches");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ ref: "main" });
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer ghp_secret123");
    expect(url).not.toContain("ghp_secret123");
    expect((init.headers as Record<string, string>)["User-Agent"]).toBeTruthy(); // GitHub requires one
  });
});

describe("dispatch", () => {
  it("succeeds on 204 at the first attempt", async () => {
    expect(await dispatch(env, "watchdog.yml", reply(204))).toMatchObject({ ok: true, status: 204, attempts: 1 });
  });

  it("retries once after a 5xx and reports success if the retry works", async () => {
    let calls = 0;
    const flaky = async () => respond(++calls === 1 ? 502 : 204);
    expect(await dispatch(env, "watchdog.yml", flaky)).toMatchObject({ ok: true, attempts: 2 });
  });

  it("does not retry a 4xx and never leaks the token into the message", async () => {
    let calls = 0;
    const bad = async () => {
      calls++;
      return new Response('{"message":"Bad credentials ghp_secret123"}', { status: 401 });
    };
    const result = await dispatch(env, "watchdog.yml", bad);
    expect(result).toMatchObject({ ok: false, status: 401, attempts: 1 });
    expect(calls).toBe(1);
    expect(result.message).not.toContain("ghp_secret123");
  });

  it("reports a network failure as status 0 after two attempts", async () => {
    const down = async () => {
      throw new Error("connect failed");
    };
    expect(await dispatch(env, "watchdog.yml", down)).toMatchObject({ ok: false, status: 0, attempts: 2 });
  });
});
