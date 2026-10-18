import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import BugFixes from "../views/BugFixes.jsx";
import FileExplorer from "../views/FileExplorer.jsx";
import Predictions from "../views/Predictions.jsx";
import { analysisId, fixtures, json, mockFetch } from "./mockApi.js";

const filesUrl = `GET /api/analysis/${analysisId}/files`;
const predictionsUrl = `GET /api/analysis/${analysisId}/predictions`;
const bugFixesUrl = `GET /api/analysis/${analysisId}/bug-fixes`;
const bodyRows = () => screen.getAllByRole("row").slice(1).filter((r) => r.querySelector("td"));
const pathsInTable = () => bodyRows().map((r) => within(r).getAllByRole("cell")[0].textContent);

describe("FileExplorer", () => {
  it("renders real file data with risk factors", async () => {
    mockFetch({ [filesUrl]: json(fixtures.files) });
    render(<FileExplorer analysisId={analysisId} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading files");
    await screen.findByRole("button", { name: "src/core.py" });
    expect(pathsInTable()).toHaveLength(fixtures.files.items.length);
    const flagged = fixtures.files.items.find((f) => f.risk_factors.length > 0);
    expect(screen.getAllByText(flagged.risk_factors[0].label).length).toBeGreaterThan(0);
  });

  it("filters by search text and shows an empty result", async () => {
    mockFetch({ [filesUrl]: json(fixtures.files) });
    render(<FileExplorer analysisId={analysisId} />);
    const search = await screen.findByLabelText("Search files");
    await userEvent.type(search, "core");
    expect(pathsInTable()).toEqual(["src/core.py"]);
    await userEvent.clear(search);
    await userEvent.type(search, "does-not-exist");
    expect(screen.getByText("No matching files")).toBeInTheDocument();
  });

  it("sorts by a column and exposes aria-sort", async () => {
    mockFetch({ [filesUrl]: json(fixtures.files) });
    render(<FileExplorer analysisId={analysisId} />);
    await screen.findByRole("button", { name: "src/core.py" });
    await userEvent.click(screen.getByRole("button", { name: /^Churn/ }));
    const byChurn = [...fixtures.files.items].sort((a, b) => b.churn - a.churn || a.path.localeCompare(b.path)).map((f) => f.path);
    expect(pathsInTable()).toEqual(byChurn);
    expect(screen.getByRole("columnheader", { name: /^Churn/ })).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(screen.getByRole("button", { name: /^Churn/ }));
    expect(screen.getByRole("columnheader", { name: /^Churn/ })).toHaveAttribute("aria-sort", "ascending");
  });

  it("explains a file's risk when expanded", async () => {
    mockFetch({ [filesUrl]: json(fixtures.files) });
    render(<FileExplorer analysisId={analysisId} />);
    const flagged = fixtures.files.items.find((f) => f.risk_factors.length > 0);
    await userEvent.click(await screen.findByRole("button", { name: flagged.path }));
    expect(screen.getByText(`Why this file is ${flagged.risk_level} risk`)).toBeInTheDocument();
    expect(screen.getByText(flagged.risk_factors[0].detail, { exact: false })).toBeInTheDocument();
  });

  it("shows an empty state when the analysis has no files", async () => {
    mockFetch({ [filesUrl]: json({ ...fixtures.files, total: 0, items: [] }) });
    render(<FileExplorer analysisId={analysisId} />);
    expect(await screen.findByText("No files to show")).toBeInTheDocument();
  });

  it("shows errors with a retry button", async () => {
    let calls = 0;
    mockFetch({
      [filesUrl]: () => (++calls === 1
        ? json({ error: { code: "analysis_not_ready", message: "The analysis is running; results are not available yet." } }, 409)
        : json(fixtures.files)),
    });
    render(<FileExplorer analysisId={analysisId} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("results are not available yet");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "src/core.py" })).toBeInTheDocument();
  });
});

describe("Predictions", () => {
  it("renders probabilities, evaluation metrics and the label disclaimer", async () => {
    mockFetch({ [predictionsUrl]: json(fixtures.predictions) });
    render(<Predictions analysisId={analysisId} />);
    expect(await screen.findByText("Heuristic labels, not verified defects")).toBeInTheDocument();
    const top = fixtures.predictions.predictions[0];
    expect(screen.getByText(`${(top.probability * 100).toFixed(1)}%`)).toBeInTheDocument();
    expect(screen.getByText("Model evaluation (chronological hold-out)")).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: /Churn baseline/ })).toBeInTheDocument();
    for (const limitation of fixtures.predictions.limitations) {
      expect(screen.getByText(limitation)).toBeInTheDocument();
    }
  });

  it("shows undefined metrics as 'undefined', not zero", async () => {
    const evaluation = { ...fixtures.predictions.evaluation };
    evaluation.models = evaluation.models.map((m) => ({ ...m, recall: null }));
    mockFetch({ [predictionsUrl]: json({ ...fixtures.predictions, evaluation }) });
    render(<Predictions analysisId={analysisId} />);
    await screen.findByText("Model evaluation (chronological hold-out)");
    expect(screen.getAllByText("undefined").length).toBeGreaterThanOrEqual(evaluation.models.length);
  });

  it("shows a meaningful empty state when no model is available", async () => {
    mockFetch({ [predictionsUrl]: json(fixtures.predictionsUnavailable) });
    render(<Predictions analysisId={analysisId} />);
    expect(await screen.findByText("No model predictions for this analysis")).toBeInTheDocument();
    expect(screen.getByText(fixtures.predictionsUnavailable.reason)).toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: /%$/ })).not.toBeInTheDocument();
    expect(screen.getByText("No evaluation available")).toBeInTheDocument();
  });
});

describe("BugFixes", () => {
  it("renders candidates with evidence and SZZ records", async () => {
    mockFetch({ [bugFixesUrl]: json(fixtures.bugFixes) });
    render(<BugFixes analysisId={analysisId} />);
    const first = fixtures.bugFixes.items[0];
    expect(await screen.findAllByText(first.subject)).not.toHaveLength(0);
    expect(screen.getAllByText(first.evidence[0]).length).toBeGreaterThan(0);
    const record = fixtures.bugFixes.items.find((b) => b.szz_records.length)?.szz_records[0];
    expect(record).toBeTruthy();
    expect(screen.getAllByText(record.inducing_hash.slice(0, 8)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Limitations:/).length).toBeGreaterThan(0);
    expect(screen.getByText("Candidates, not confirmed bugs")).toBeInTheDocument();
  });

  it("shows an empty state when nothing was detected", async () => {
    mockFetch({ [bugFixesUrl]: json({ ...fixtures.bugFixes, total: 0, items: [] }) });
    render(<BugFixes analysisId={analysisId} />);
    expect(await screen.findByText("No bug-fix candidates")).toBeInTheDocument();
    expect(screen.getByText(/found no likely or uncertain bug-fix commits/)).toBeInTheDocument();
  });

  it("filters by search text", async () => {
    mockFetch({ [bugFixesUrl]: json(fixtures.bugFixes) });
    render(<BugFixes analysisId={analysisId} />);
    await userEvent.type(await screen.findByLabelText("Search commits"), "zzz-nothing");
    expect(screen.getByText("No candidates match the current filters.")).toBeInTheDocument();
  });
});
