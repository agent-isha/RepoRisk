import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { formatDateTime } from "../lib/format.js";
import { Card, ErrorState, Spinner, inputClass, primaryButtonClass } from "./ui.jsx";

export const DEFAULT_OPTIONS = {
  exclude_tests: true,
  source_only: true,
  run_szz: true,
  train_model: true,
  horizon_days: 90,
  snapshot_interval_days: 90,
};

const ACTIVE = new Set(["queued", "running"]);

// Pick a repository, start an analysis and follow its job status until it finishes.
export default function AnalysisControls({ repositories, current, onStatus, pollMs = 1500 }) {
  const firstUsable = repositories.find((r) => r.available)?.id || "";
  const [repoId, setRepoId] = useState(firstUsable);
  const [options, setOptions] = useState(DEFAULT_OPTIONS);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  useEffect(() => {
    if (!repoId && firstUsable) setRepoId(firstUsable);
  }, [repoId, firstUsable]);

  const running = Boolean(current && ACTIVE.has(current.status));
  const currentId = current?.id;

  // Poll while the current analysis is queued or running. The loop schedules
  // itself, so it doesn't depend on each response being a new object.
  useEffect(() => {
    if (!running) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const status = await api.status(currentId);
        if (cancelled) return;
        onStatus(status);
        if (ACTIVE.has(status.status)) timer.current = setTimeout(tick, pollMs);
      } catch (err) {
        if (!cancelled) setError(err);
      }
    };
    timer.current = setTimeout(tick, pollMs);
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [running, currentId, onStatus, pollMs]);

  async function start(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      onStatus(await api.startAnalysis(repoId, options));
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  const setOption = (key, value) => setOptions((o) => ({ ...o, [key]: value }));

  return (
    <Card title="Repository analysis" description="Only repositories configured on the server (REPORISK_REPOS) can be analyzed.">
      {repositories.length === 0 ? (
        <ErrorState
          title="No repositories configured"
          error={{
            message:
              "Start the backend with REPORISK_REPOS set to one or more local git repositories, then reload this page.",
          }}
        />
      ) : (
        <form onSubmit={start} className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <label htmlFor="repository" className="mb-1 block text-sm font-medium text-slate-700">
                Repository
              </label>
              <select
                id="repository"
                className={inputClass}
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
                disabled={running || submitting}
              >
                {repositories.map((r) => (
                  <option key={r.id} value={r.id} disabled={!r.available}>
                    {r.name} — {r.available ? r.path : `unavailable: ${r.reason}`}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className={primaryButtonClass} disabled={!repoId || running || submitting}>
              {(running || submitting) && <Spinner className="h-4 w-4 text-white" />}
              {running ? "Analysis running…" : "Start analysis"}
            </button>
          </div>

          <details className="rounded-lg border border-slate-200 px-4 py-3 text-sm">
            <summary className="cursor-pointer font-medium text-slate-700">Analysis options</summary>
            <fieldset className="mt-3 grid gap-3 sm:grid-cols-2" disabled={running || submitting}>
              <legend className="sr-only">Analysis options</legend>
              {[
                ["source_only", "Source-code files only"],
                ["exclude_tests", "Exclude test files"],
                ["run_szz", "Trace bug-inducing commits (SZZ)"],
                ["train_model", "Build dataset and train ML model"],
              ].map(([key, label]) => (
                <label key={key} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                    checked={options[key]}
                    onChange={(e) => setOption(key, e.target.checked)}
                  />
                  {label}
                </label>
              ))}
              <label className="block">
                <span className="mb-1 block text-slate-700">Prediction horizon (days)</span>
                <input
                  type="number"
                  min="7"
                  max="730"
                  className={inputClass}
                  value={options.horizon_days}
                  onChange={(e) => setOption("horizon_days", Number(e.target.value))}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-slate-700">Snapshot interval (days)</span>
                <input
                  type="number"
                  min="7"
                  max="365"
                  className={inputClass}
                  value={options.snapshot_interval_days}
                  onChange={(e) => setOption("snapshot_interval_days", Number(e.target.value))}
                />
              </label>
            </fieldset>
          </details>
        </form>
      )}

      {error && (
        <div className="mt-4">
          <ErrorState title="Could not start or follow the analysis" error={error} />
        </div>
      )}
      {current && <JobStatus job={current} />}
    </Card>
  );
}

function JobStatus({ job }) {
  const percent = Math.round((job.stage_index / job.stage_count) * 100);
  if (job.status === "failed") {
    return (
      <div className="mt-4">
        <ErrorState title={`Analysis of ${job.repository_name} failed`} error={{ message: job.error }} />
      </div>
    );
  }
  if (job.status === "completed") {
    return (
      <p role="status" className="mt-4 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        Analysis of <strong>{job.repository_name}</strong> completed {formatDateTime(job.finished_at)}.
      </p>
    );
  }
  return (
    <div role="status" aria-live="polite" className="mt-4 space-y-2 text-sm">
      <div className="flex justify-between text-slate-600">
        <span>
          {job.status === "queued" ? "Queued…" : job.stage_label || "Starting…"}
          {job.message ? ` — ${job.message}` : ""}
        </span>
        <span className="tabular-nums">
          Step {job.stage_index} of {job.stage_count}
        </span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-label="Analysis progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div className="h-full rounded-full bg-indigo-600 transition-all" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
