import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL, ApiError, api } from "../api/client.js";
import { json, mockFetch } from "./mockApi.js";

describe("api client", () => {
  it("uses the configured base URL and query parameters", async () => {
    const fetch = mockFetch({ "GET /api/analysis/abc/files": json({ items: [] }) });
    await api.files("abc", { limit: 10, search: "" });
    expect(fetch.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/analysis/abc/files?limit=10`);
  });

  it("sends the analysis request body", async () => {
    const fetch = mockFetch({ "POST /api/analyze": json({ id: "x" }, 202) });
    await api.startAnalysis("demo-repo", { run_szz: false });
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ repository_id: "demo-repo", options: { run_szz: false } });
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
