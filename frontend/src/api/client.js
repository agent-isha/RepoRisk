// Thin wrapper around the RepoRisk FastAPI backend.
// The base URL comes from VITE_API_BASE_URL (see .env.example).

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(message, { status = null, code = "error", details = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function query(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    throw new ApiError(`Cannot reach the RepoRisk API at ${API_BASE_URL}. Is the backend running?`, {
      code: "network_error",
    });
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    // non-JSON body; handled below
  }
  if (!response.ok) {
    const error = body && body.error;
    throw new ApiError(error?.message || `Request failed with status ${response.status}.`, {
      status: response.status,
      code: error?.code || "http_error",
      details: error?.details ?? null,
    });
  }
  return body;
}

const id = (value) => encodeURIComponent(value);

export const api = {
  health: () => request("/health"),
  repositories: () => request("/api/repositories"),
  analyses: () => request("/api/analyses"),
  startAnalysis: (repositoryId, options) =>
    request("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ repository_id: repositoryId, options }),
    }),
  status: (analysisId) => request(`/api/analysis/${id(analysisId)}`),
  summary: (analysisId) => request(`/api/analysis/${id(analysisId)}/summary`),
  files: (analysisId, params = { limit: 5000 }) =>
    request(`/api/analysis/${id(analysisId)}/files${query(params)}`),
  predictions: (analysisId, params = { limit: 5000 }) =>
    request(`/api/analysis/${id(analysisId)}/predictions${query(params)}`),
  bugFixes: (analysisId, params = { limit: 1000 }) =>
    request(`/api/analysis/${id(analysisId)}/bug-fixes${query(params)}`),
};
