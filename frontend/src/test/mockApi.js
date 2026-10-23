import { vi } from "vitest";
import analysisStatus from "./fixtures/analysis-status.json";
import bugFixes from "./fixtures/bug-fixes.json";
import files from "./fixtures/files.json";
import health from "./fixtures/health.json";
import predictionsUnavailable from "./fixtures/predictions-unavailable.json";
import predictions from "./fixtures/predictions.json";
import repositories from "./fixtures/repositories.json";
import session from "./fixtures/session.json";
import summary from "./fixtures/summary.json";

// Fixtures are real API responses captured by scripts/capture_frontend_fixtures.py
// and validated against the backend schemas in tests/test_api.py.
export const fixtures = {
  analysisStatus, bugFixes, files, health, predictions, predictionsUnavailable, repositories, session, summary,
};

export const json = (body, status = 200) => ({ status, body });

export const notSignedIn = json({ error: { code: "not_authenticated", message: "Sign in to continue." } }, 401);

// Replace fetch with a router: routes map "METHOD /path" (query ignored) to a
// response or a function returning one. Unmatched requests fail loudly.
export function mockFetch(routes) {
  const fn = vi.fn(async (url, options = {}) => {
    const { pathname } = new URL(url, "http://localhost");
    const key = `${(options.method || "GET").toUpperCase()} ${pathname}`;
    let route = routes[key];
    if (typeof route === "function") route = await route(url, options);
    if (route === undefined) throw new Error(`unmocked request: ${key}`);
    if (route instanceof Error) throw route;
    const { status = 200, body } = route;
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const analysisId = analysisStatus.id;

// Routes for a signed-in user with the workspace loaded.
export const signedIn = (analyses = []) => ({
  "GET /api/auth/me": json(fixtures.session),
  "GET /health": json(fixtures.health),
  "GET /api/repositories": json(fixtures.repositories),
  "GET /api/analyses": json(analyses),
});

// Requests made to a path with a given method, for asserting on headers/bodies.
export const callsTo = (fetchMock, method, path) =>
  fetchMock.mock.calls.filter(
    ([url, options = {}]) => new URL(url, "http://localhost").pathname === path && (options.method || "GET") === method,
  );
