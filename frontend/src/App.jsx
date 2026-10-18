import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL, api } from "./api/client.js";
import AnalysisControls from "./components/AnalysisControls.jsx";
import { Badge, EmptyState, ErrorState, LoadingState } from "./components/ui.jsx";
import { useApi } from "./hooks/useApi.js";
import BugFixes from "./views/BugFixes.jsx";
import FileExplorer from "./views/FileExplorer.jsx";
import Overview from "./views/Overview.jsx";
import Predictions from "./views/Predictions.jsx";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "files", label: "File risk explorer" },
  { id: "predictions", label: "ML predictions" },
  { id: "bugfixes", label: "Bug-fix insights" },
];

export default function App({ pollMs }) {
  const boot = useApi(
    () => Promise.all([api.health(), api.repositories(), api.analyses()]),
    [],
  );
  const [current, setCurrent] = useState(null);
  const [analyses, setAnalyses] = useState([]);
  const [tab, setTab] = useState("overview");

  // On load, show the most recent completed analysis, or follow one still running.
  useEffect(() => {
    if (!boot.data) return;
    const list = boot.data[2];
    setAnalyses(list);
    setCurrent((c) => c || list.find((a) => a.status !== "failed") || null);
  }, [boot.data]);

  const onStatus = useCallback((status) => {
    setCurrent(status);
    setAnalyses((list) => [status, ...list.filter((a) => a.id !== status.id)]);
  }, []);

  const completedId = current?.status === "completed" ? current.id : null;
  const summary = useApi(completedId ? () => api.summary(completedId) : null, [completedId]);

  if (boot.loading) return <Shell><LoadingState label="Connecting to the RepoRisk API…" /></Shell>;
  if (boot.error) {
    return (
      <Shell>
        <ErrorState title="Cannot load RepoRisk" error={boot.error} onRetry={boot.reload} />
        <p className="mt-4 text-sm text-slate-600">
          The dashboard expects the API at <code className="font-mono">{API_BASE_URL}</code>. Start it with{" "}
          <code className="font-mono">python -m uvicorn reporisk.api.app:app --port 8000</code> (see DEVELOPMENT.md),
          or set <code className="font-mono">VITE_API_BASE_URL</code>.
        </p>
      </Shell>
    );
  }

  const [health, repositories] = boot.data;

  return (
    <Shell health={health}>
      <AnalysisControls repositories={repositories} current={current} onStatus={onStatus} pollMs={pollMs} />

      {!completedId ? (
        <div className="mt-6">
          <EmptyState title={current ? "Results will appear when the analysis finishes" : "No analysis yet"}>
            {current
              ? "Analysis runs in the background on the server; you can leave this page open."
              : "Choose a permitted repository and start an analysis to see risk, bug-fix and model results."}
          </EmptyState>
        </div>
      ) : (
        <div className="mt-6">
          <nav aria-label="Result views" className="mb-6 flex gap-1 overflow-x-auto border-b border-slate-200">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                aria-current={tab === t.id ? "page" : undefined}
                onClick={() => setTab(t.id)}
                className={`whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-indigo-600 ${
                  tab === t.id
                    ? "border-indigo-600 text-indigo-700"
                    : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
          {tab === "overview" && (
            summary.loading ? <LoadingState label="Loading summary…" />
              : summary.error ? <ErrorState error={summary.error} onRetry={summary.reload} title="Could not load the summary" />
                : summary.data && (
                  <Overview summary={summary.data} analyses={analyses} currentId={completedId}
                            onSelectAnalysis={(a) => setCurrent(a)} onNavigate={setTab} />
                )
          )}
          {tab === "files" && <FileExplorer analysisId={completedId} />}
          {tab === "predictions" && <Predictions analysisId={completedId} />}
          {tab === "bugfixes" && <BugFixes analysisId={completedId} />}
        </div>
      )}
    </Shell>
  );
}

function Shell({ health, children }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">RepoRisk</h1>
            <p className="text-sm text-slate-500">Git-history risk analysis for local repositories</p>
          </div>
          {health && (
            <div className="flex items-center gap-2 text-sm">
              <Badge tone="success">API {health.version}</Badge>
              <Badge tone={health.ml_available ? "info" : "neutral"}
                     title={health.ml_available ? `scikit-learn ${health.scikit_learn_version}` : "scikit-learn is not installed"}>
                ML {health.ml_available ? "available" : "unavailable"}
              </Badge>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
    </div>
  );
}
