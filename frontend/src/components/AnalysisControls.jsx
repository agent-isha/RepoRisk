import { useState } from "react";
import { api } from "../api/client.js";
import { formatDateTime } from "../lib/format.js";
import { PlayIcon } from "./icons.jsx";
import { Badge, Button, Card, ErrorState, Spinner, inputClass } from "./ui.jsx";

export const DEFAULT_OPTIONS = {
  exclude_tests: true,
  source_only: true,
  run_szz: true,
  train_model: true,
  horizon_days: 90,
  snapshot_interval_days: 90,
};

export const ACTIVE = new Set(["queued", "running"]);
const STATUS_TONES = { completed: "success", failed: "danger", running: "info", queued: "neutral" };

// Mirrors the server's check (reporisk/api/github.py); the server remains the authority.
const GITHUB_URL = /^https:\/\/(www\.)?github\.com(:443)?\/[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}\/[A-Za-z0-9._-]{1,100}\/?$/i;

export function githubUrlError(value) {
  const url = value.trim();
  if (!url) return "Enter a GitHub repository URL, like https://github.com/psf/requests.";
  if (!/^https:\/\//i.test(url)) return "Only https:// GitHub URLs are supported.";
  if (!GITHUB_URL.test(url) || /\/\.\.?(\.git)?\/?$/.test(url)) {
    return "Use a public repository's main URL: https://github.com/owner/repo.";
  }
  return null;
}

// Pick a repository (a public GitHub URL, or one configured on the server) and
// start an analysis. Job polling lives in the dashboard, so progress keeps
// updating while you browse other pages.
export function AnalysisForm({ repositories, current, onStatus }) {
  const firstUsable = repositories.find((r) => r.available)?.id || "";
  const [source, setSource] = useState(firstUsable ? "configured" : "github");
  const [repoId, setRepoId] = useState(firstUsable);
  const [githubUrl, setGithubUrl] = useState("");
  const [urlError, setUrlError] = useState(null);
  const [options, setOptions] = useState(DEFAULT_OPTIONS);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const running = Boolean(current && ACTIVE.has(current.status));
  const busy = running || submitting;

  async function start(event) {
    event.preventDefault();
    setError(null);
    if (source === "github") {
      const problem = githubUrlError(githubUrl);
      setUrlError(problem);
      if (problem) {
        document.getElementById("github-url")?.focus();
        return;
      }
    }
    setSubmitting(true);
    try {
      onStatus(source === "github"
        ? await api.startGithubAnalysis(githubUrl.trim(), options)
        : await api.startAnalysis(repoId || firstUsable, options));
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  const setOption = (key, value) => setOptions((o) => ({ ...o, [key]: value }));
  const submit = (
    <Button type="submit" disabled={busy || (source === "configured" && !firstUsable)}>
      {busy ? <Spinner className="h-4 w-4 text-white" /> : <PlayIcon className="h-4 w-4" />}
      {running ? "Analysis running…" : "Start analysis"}
    </Button>
  );

  return (
    <Card title="Start an analysis" description="Analyze a public GitHub repository, or a repository configured on the server.">
      <form onSubmit={start} noValidate className="space-y-5">
        {repositories.length > 0 && (
          <div role="radiogroup" aria-label="Repository source" className="inline-flex rounded-lg border border-line bg-raised/40 p-1 text-sm">
            {[["github", "GitHub URL"], ["configured", "Configured repository"]].map(([value, label]) => (
              <label key={value} className={`cursor-pointer rounded-md px-3 py-1.5 transition-colors ${source === value ? "bg-indigo-500/20 text-white" : "text-slate-400 hover:text-slate-200"}`}>
                <input type="radio" name="source" value={value} className="sr-only" checked={source === value}
                       disabled={busy} onChange={() => { setSource(value); setError(null); }} />
                {label}
              </label>
            ))}
          </div>
        )}

        {source === "github" ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
            <div className="flex-1">
              <label htmlFor="github-url" className="mb-1.5 block text-sm font-medium text-slate-300">GitHub repository URL</label>
              <input id="github-url" type="url" inputMode="url" autoComplete="off" spellCheck="false"
                     placeholder="https://github.com/psf/requests" className={inputClass} value={githubUrl} disabled={busy}
                     aria-invalid={urlError ? true : undefined}
                     aria-describedby={urlError ? "github-url-error" : "github-url-hint"}
                     onChange={(e) => { setGithubUrl(e.target.value); if (urlError) setUrlError(null); }} />
              {urlError ? (
                <p id="github-url-error" className="mt-1.5 text-xs text-red-300">{urlError}</p>
              ) : (
                <p id="github-url-hint" className="mt-1.5 text-xs text-slate-500">
                  Public repositories on github.com only. The server clones it into a temporary folder, analyzes its
                  history, then deletes the clone.
                </p>
              )}
            </div>
            <div className="sm:pt-[26px]">{submit}</div>
          </div>
        ) : (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <label htmlFor="repository" className="mb-1.5 block text-sm font-medium text-slate-300">Repository</label>
              <select id="repository" className={inputClass} value={repoId} disabled={busy}
                      onChange={(e) => setRepoId(e.target.value)}>
                {repositories.map((r) => (
                  <option key={r.id} value={r.id} disabled={!r.available}>
                    {r.name} — {r.available ? r.path : `unavailable: ${r.reason}`}
                  </option>
                ))}
              </select>
            </div>
            {submit}
          </div>
        )}

        <fieldset disabled={busy} className="grid gap-x-6 gap-y-3 rounded-lg border border-line bg-raised/40 p-4 text-sm sm:grid-cols-2">
          <legend className="px-1 text-xs font-medium uppercase tracking-wide text-slate-400">Analysis options</legend>
          {[
            ["source_only", "Source-code files only"],
            ["exclude_tests", "Exclude test files"],
            ["run_szz", "Trace bug-inducing commits (SZZ)"],
            ["train_model", "Build dataset and train ML model"],
          ].map(([key, label]) => (
            <label key={key} className="flex items-center gap-2.5 text-slate-300">
              <input type="checkbox" checked={options[key]} onChange={(e) => setOption(key, e.target.checked)}
                     className="h-4 w-4 rounded border-line-strong bg-raised accent-indigo-500" />
              {label}
            </label>
          ))}
          <label className="block">
            <span className="mb-1.5 block text-slate-300">Prediction horizon (days)</span>
            <input type="number" min="7" max="730" className={inputClass} value={options.horizon_days}
                   onChange={(e) => setOption("horizon_days", Number(e.target.value))} />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-slate-300">Snapshot interval (days)</span>
            <input type="number" min="7" max="365" className={inputClass} value={options.snapshot_interval_days}
                   onChange={(e) => setOption("snapshot_interval_days", Number(e.target.value))} />
          </label>
        </fieldset>
      </form>
      {error && <div className="mt-4"><ErrorState title="Could not start the analysis" error={error} /></div>}
      {current && <JobStatus job={current} />}
    </Card>
  );
}

export function JobStatus({ job }) {
  const percent = Math.round((job.stage_index / job.stage_count) * 100);
  if (job.status === "failed") {
    return (
      <div className="mt-4">
        <ErrorState
          title={job.stage === "cloning" ? `Could not clone ${job.repository_name}` : `Analysis of ${job.repository_name} failed`}
          error={{ message: job.error }}
        />
      </div>
    );
  }
  if (job.status === "completed") {
    return (
      <p role="status" className="mt-4 rounded-lg border border-emerald-400/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">
        Analysis of <strong>{job.repository_name}</strong> completed {formatDateTime(job.finished_at)}.
      </p>
    );
  }
  return (
    <div role="status" aria-live="polite" className="mt-4 space-y-2 text-sm">
      <div className="flex justify-between gap-4 text-slate-300">
        <span>
          {job.status === "queued" ? "Queued…" : job.stage_label || "Starting…"}
          {job.message ? ` — ${job.message}` : ""}
        </span>
        <span className="shrink-0 tabular-nums text-slate-400">Step {job.stage_index} of {job.stage_count}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-raised" role="progressbar" aria-label="Analysis progress"
           aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <div className="h-full rounded-full bg-indigo-400 transition-all duration-500" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

export function AnalysisHistory({ analyses, currentId, onSelect }) {
  if (!analyses.length) return null;
  return (
    <Card title="Recent analyses" description="Kept in server memory until the API restarts. Shared by all signed-in users.">
      <ul className="-my-2 divide-y divide-line text-sm">
        {analyses.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
            <span className="min-w-0">
              <span className="font-medium text-slate-100">{a.repository_name}</span>{" "}
              <span className="text-slate-500">{formatDateTime(a.created_at)}</span>
            </span>
            <span className="flex items-center gap-3">
              <Badge tone={STATUS_TONES[a.status]}>{a.status}</Badge>
              {a.id === currentId ? (
                <span className="text-xs text-slate-500">selected</span>
              ) : (
                a.status === "completed" && (
                  <Button variant="ghost" size="sm" onClick={() => onSelect(a)}>View results</Button>
                )
              )}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
