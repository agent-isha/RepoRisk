import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App.jsx";
import { analysisId, fixtures, json, mockFetch } from "./mockApi.js";

const base = {
  "GET /health": json(fixtures.health),
  "GET /api/repositories": json(fixtures.repositories),
};

describe("App", () => {
  it("shows a loading state, then an empty state when nothing has been analyzed", async () => {
    mockFetch({ ...base, "GET /api/analyses": json([]) });
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Connecting to the RepoRisk API");
    expect(await screen.findByText("No analysis yet")).toBeInTheDocument();
    expect(screen.getByLabelText("Repository")).toHaveValue("demo-repo");
    expect(screen.getByText("ML available")).toBeInTheDocument();
  });

  it("shows a helpful error when the backend is unreachable", async () => {
    mockFetch({ "GET /health": new TypeError("Failed to fetch"), "GET /api/repositories": json([]), "GET /api/analyses": json([]) });
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cannot reach the RepoRisk API");
    expect(screen.getByText(/python -m uvicorn reporisk.api.app:app/)).toBeInTheDocument();
  });

  it("explains how to configure repositories when none are permitted", async () => {
    mockFetch({ "GET /health": json(fixtures.health), "GET /api/repositories": json([]), "GET /api/analyses": json([]) });
    render(<App />);
    expect(await screen.findByText("No repositories configured")).toBeInTheDocument();
  });

  it("starts an analysis, follows its progress and shows the results", async () => {
    const running = { ...fixtures.analysisStatus, status: "running", stage: "tracing_szz",
      stage_label: "Tracing candidate bug-inducing commits", stage_index: 4, message: "10 of 24 likely bug fixes", finished_at: null };
    let polls = 0;
    mockFetch({
      ...base,
      "GET /api/analyses": json([]),
      "POST /api/analyze": json(running, 202),
      [`GET /api/analysis/${analysisId}`]: () => json(++polls < 2 ? running : fixtures.analysisStatus),
      [`GET /api/analysis/${analysisId}/summary`]: json(fixtures.summary),
    });
    render(<App pollMs={5} />);
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));

    expect(await screen.findByText(/Tracing candidate bug-inducing commits — 10 of 24/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "67");
    expect(await screen.findByText(/completed/, { selector: "p" })).toBeInTheDocument();

    const highCard = (await screen.findByText("High-risk files")).parentElement;
    expect(within(highCard).getByText(String(fixtures.summary.risk_counts.high))).toBeInTheDocument();
    expect(screen.getByText(String(fixtures.summary.commits_analyzed))).toBeInTheDocument();
  });

  it("shows why an analysis failed", async () => {
    const failed = { ...fixtures.analysisStatus, status: "failed", error: "GitCommandError: bad object" };
    mockFetch({ ...base, "GET /api/analyses": json([]), "POST /api/analyze": json(failed, 202) });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));
    expect(await screen.findByText(/bad object/)).toBeInTheDocument();
  });

  it("shows API validation errors when an analysis cannot start", async () => {
    mockFetch({
      ...base,
      "GET /api/analyses": json([]),
      "POST /api/analyze": json({ error: { code: "repository_unavailable", message: "Repository 'demo-repo' cannot be analyzed: not a git repository." } }, 409),
    });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not a git repository");
  });

  it("opens the latest completed analysis and switches tabs", async () => {
    mockFetch({
      ...base,
      "GET /api/analyses": json([fixtures.analysisStatus]),
      [`GET /api/analysis/${analysisId}/summary`]: json(fixtures.summary),
      [`GET /api/analysis/${analysisId}/files`]: json(fixtures.files),
    });
    render(<App />);
    expect(await screen.findByText("Heuristic risk distribution")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "File risk explorer" }));
    expect(await screen.findByRole("button", { name: "src/core.py" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "File risk explorer" })).toHaveAttribute("aria-current", "page");
  });
});
