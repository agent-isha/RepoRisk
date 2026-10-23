// Thin wrapper around the RepoRisk FastAPI backend.
//
// Authentication uses an HttpOnly session cookie that JavaScript cannot read.
// The per-session CSRF token is kept only in memory (never localStorage) and
// sent in X-CSRF-Token on state-changing requests.

// Empty means "same origin": Vite proxies /api in development, and in production
// the API serves the built frontend. Override with VITE_API_BASE_URL if needed.
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");
const API_LABEL = API_BASE_URL || "this site's /api";

export class ApiError extends Error {
  constructor(message, { status = null, code = "error", details = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let csrfToken = null;
let unauthorizedHandler = null;

export function setCsrfToken(token) {
  csrfToken = token || null;
}

// Called when an authenticated request comes back 401 (session expired or revoked).
export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
  return () => {
    if (unauthorizedHandler === handler) unauthorizedHandler = null;
  };
}

function query(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function request(path, { method = "GET", body, notifyUnauthorized = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
    });
  } catch {
    throw new ApiError(`Cannot reach the RepoRisk API (${API_LABEL}). Is the backend running?`, {
      code: "network_error",
    });
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    // non-JSON body; handled below
  }
  if (!response.ok) {
    const error = data && data.error;
    const apiError = new ApiError(error?.message || `Request failed with status ${response.status}.`, {
      status: response.status,
      code: error?.code || "http_error",
      details: error?.details ?? null,
    });
    if (response.status === 401 && notifyUnauthorized && unauthorizedHandler) unauthorizedHandler(apiError);
    throw apiError;
  }
  return data;
}

const id = (value) => encodeURIComponent(value);

export const api = {
  // auth: a 401 here is an answer ("not signed in"), not an expired session
  me: () => request("/api/auth/me", { notifyUnauthorized: false }),
  login: (email, password) =>
    request("/api/auth/login", { method: "POST", body: { email, password }, notifyUnauthorized: false }),
  logout: () => request("/api/auth/logout", { method: "POST", notifyUnauthorized: false }),

  health: () => request("/health"),
  repositories: () => request("/api/repositories"),
  analyses: () => request("/api/analyses"),
  startAnalysis: (repositoryId, options) =>
    request("/api/analyze", { method: "POST", body: { repository_id: repositoryId, options } }),
  startGithubAnalysis: (githubUrl, options) =>
    request("/api/analyze", { method: "POST", body: { github_url: githubUrl, options } }),
  status: (analysisId) => request(`/api/analysis/${id(analysisId)}`),
  summary: (analysisId) => request(`/api/analysis/${id(analysisId)}/summary`),
  files: (analysisId, params = { limit: 5000 }) =>
    request(`/api/analysis/${id(analysisId)}/files${query(params)}`),
  predictions: (analysisId, params = { limit: 5000 }) =>
    request(`/api/analysis/${id(analysisId)}/predictions${query(params)}`),
  bugFixes: (analysisId, params = { limit: 1000 }) =>
    request(`/api/analysis/${id(analysisId)}/bug-fixes${query(params)}`),
};
