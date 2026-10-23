import { afterEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL, ApiError, api, onUnauthorized, setCsrfToken } from "../api/client.js";
import { callsTo, json, mockFetch } from "./mockApi.js";

afterEach(() => setCsrfToken(null));

describe("api client", () => {
  it("calls the same origin by default with cookies included", async () => {
    const fetch = mockFetch({ "GET /api/analysis/abc/files": json({ items: [] }) });
    await api.files("abc", { limit: 10, search: "" });
    const [url, options] = fetch.mock.calls[0];
    expect(API_BASE_URL).toBe("");
    expect(url).toBe("/api/analysis/abc/files?limit=10");
    expect(options.credentials).toBe("include");
  });

  it("sends the CSRF token on POST but not on GET", async () => {
    const fetch = mockFetch({ "POST /api/analyze": json({ id: "x" }, 202), "GET /api/analyses": json([]) });
    setCsrfToken("token-123");
    await api.startAnalysis("demo-repo", { run_szz: false });
    await api.analyses();
    const [, post] = callsTo(fetch, "POST", "/api/analyze")[0];
    expect(post.headers["X-CSRF-Token"]).toBe("token-123");
    expect(JSON.parse(post.body)).toEqual({ repository_id: "demo-repo", options: { run_szz: false } });
    const [, get] = callsTo(fetch, "GET", "/api/analyses")[0];
    expect(get.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("turns API error bodies into ApiError", async () => {
    mockFetch({
      "POST /api/analyze": json({
        error: { code: "validation_error", message: "The request is invalid.", details: [{ location: "body.x", message: "bad" }] },
      }, 422),
    });
    const error = await api.startAnalysis("x", {}).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "validation_error", message: "The request is invalid." });
    expect(error.details).toHaveLength(1);
  });

  it("reports expired sessions on data requests, not on auth checks", async () => {
    const handler = vi.fn();
    const stop = onUnauthorized(handler);
    const expired = json({ error: { code: "not_authenticated", message: "Sign in to continue." } }, 401);
    mockFetch({ "GET /api/repositories": expired, "GET /api/auth/me": expired, "POST /api/auth/login": expired });
    await api.me().catch(() => {});
    await api.login("a@b.co", "x").catch(() => {});
    expect(handler).not.toHaveBeenCalled();
    await api.repositories().catch(() => {});
    expect(handler).toHaveBeenCalledTimes(1);
    stop();
  });

  it("explains network failures", async () => {
    mockFetch({ "GET /health": new TypeError("Failed to fetch") });
    const error = await api.health().catch((e) => e);
    expect(error.code).toBe("network_error");
    expect(error.message).toContain("Is the backend running?");
  });

  it("handles non-JSON error responses", async () => {
    vi.stubGlobal("fetch", async () => ({ ok: false, status: 502, json: async () => { throw new Error("html"); } }));
    const error = await api.health().catch((e) => e);
    expect(error.message).toBe("Request failed with status 502.");
  });
});
