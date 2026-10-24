import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client.js";
import { AuthProvider, useAuth } from "./auth/AuthContext.jsx";
import { ACTIVE, AnalysisForm, AnalysisHistory } from "./components/AnalysisControls.jsx";
import AppShell, { NAV } from "./components/AppShell.jsx";
import { LogoMark, PlayIcon } from "./components/icons.jsx";
import { Button, EmptyState, ErrorState, LoadingState, PageHeader } from "./components/ui.jsx";
import { useApi } from "./hooks/useApi.js";
import { useHashRoute } from "./hooks/useHashRoute.js";
import BugFixes from "./views/BugFixes.jsx";
import FileExplorer from "./views/FileExplorer.jsx";
import LoginPage from "./views/LoginPage.jsx";
import Overview from "./views/Overview.jsx";
import Predictions from "./views/Predictions.jsx";

const ROUTES = NAV.map((n) => n.id);

export default function App({ pollMs = 1500 }) {
  return (
    <AuthProvider>
      <AuthGate pollMs={pollMs} />
    </AuthProvider>
  );
}

// Nothing behind the login page renders (or fetches) until the server confirms a session.
function AuthGate({ pollMs }) {
  const auth = useAuth();
  if (auth.status === "checking") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <LogoMark className="h-10 w-10" />
        <LoadingState label="Checking your session…" />
      </div>
    );
  }
  if (auth.status === "error") {
    return (
      <div className="mx-auto flex min-h-screen max-w-lg flex-col justify-center px-4">
        <ErrorState title="Cannot reach RepoRisk" error={auth.error} onRetry={auth.retry} />
        {import.meta.env.DEV ? (
          <p className="mt-4 text-sm text-slate-400">
            Start the API with <code className="font-mono text-slate-300">python -m uvicorn reporisk.api.main:app --port 8000</code>{" "}
            (see DEVELOPMENT.md) and keep <code className="font-mono text-slate-300">npm run dev</code> running.
          </p>
        ) : (
          <p className="mt-4 text-sm text-slate-400">
            The RepoRisk server didn&rsquo;t respond. If it has been idle it may take up to a minute to start, so try
            again shortly. If this persists, the API may not be deployed or connected to this site yet.
          </p>
        )}
      </div>
    );
  }
  if (auth.status === "signedOut") return <LoginPage />;
  return <Dashboard user={auth.user} onLogout={auth.logout} pollMs={pollMs} />;
}

function Dashboard({ user, onLogout, pollMs }) {
  const [route, navigate] = useHashRoute(ROUTES, "overview");
  const boot = useApi(() => Promise.all([api.health(), api.repositories(), api.analyses()]), []);
  const [current, setCurrent] = useState(null);
  const [analyses, setAnalyses] = useState([]);
  const [pollError, setPollError] = useState(null);

  // Show the most recent analysis that didn't fail (completed, or one still running).
  useEffect(() => {
    if (!boot.data) return;
    const list = boot.data[2];
    setAnalyses(list);
    setCurrent((c) => c || list.find((a) => a.status !== "failed") || null);
  }, [boot.data]);

  const onStatus = useCallback((status) => {
    setPollError(null);
    setCurrent(status);
    setAnalyses((list) => [status, ...list.filter((a) => a.id !== status.id)]);
  }, []);

  useJobPolling(current, onStatus, setPollError, pollMs);

  const completedId = current?.status === "completed" ? current.id : null;
  const summary = useApi(completedId ? () => api.summary(completedId) : null, [completedId]);
  const health = boot.data?.[0];

  let page;
  if (boot.loading) page = <LoadingState label="Loading workspace…" />;
  else if (boot.error) page = <ErrorState title="Could not load RepoRisk" error={boot.error} onRetry={boot.reload} />;
  else if (route === "analyses") {
    page = (
      <>
        <PageHeader title="Run analysis" description="Mine a permitted repository's git history, score file risk, detect bug fixes, trace them with SZZ, and train the model." />
        <div className="space-y-6">
          <AnalysisForm repositories={boot.data[1]} current={current} onStatus={onStatus} />
          {pollError && <ErrorState title="Lost track of the running analysis" error={pollError} />}
          <AnalysisHistory analyses={analyses} currentId={current?.id}
                           onSelect={(a) => { setCurrent(a); navigate("overview"); }} />
        </div>
      </>
    );
  } else if (!completedId) {
    const running = current && ACTIVE.has(current.status);
    page = (
      <EmptyState
        icon={<PlayIcon className="h-5 w-5" />}
        title={running ? "Results will appear when the analysis finishes" : "No analysis results yet"}
        action={<Button onClick={() => navigate("analyses")}>{running ? "View progress" : "Run an analysis"}</Button>}
      >
        {running
          ? "The analysis is running on the server. You can keep browsing; progress is shown in the header."
          : "Choose a permitted repository and start an analysis to see risk, bug-fix and model results."}
      </EmptyState>
    );
  } else if (route === "overview") {
    page = summary.loading ? <LoadingState label="Loading summary…" />
      : summary.error ? <ErrorState error={summary.error} onRetry={summary.reload} title="Could not load the summary" />
        : summary.data && <Overview summary={summary.data} onNavigate={navigate} />;
  } else if (route === "files") page = <FileExplorer analysisId={completedId} />;
  else if (route === "predictions") page = <Predictions analysisId={completedId} />;
  else if (route === "bug-fixes") page = <BugFixes analysisId={completedId} />;

  return (
    <AppShell route={route} onNavigate={navigate} user={user} onLogout={onLogout} health={health} current={current}>
      {page}
    </AppShell>
  );
}

// Poll the current job while it is queued or running. The loop reschedules
// itself, so it doesn't depend on each response being a new object.
function useJobPolling(current, onStatus, onError, pollMs) {
  const timer = useRef(null);
  const running = Boolean(current && ACTIVE.has(current.status));
  const currentId = current?.id;
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
        if (!cancelled) onError(err);
      }
    };
    timer.current = setTimeout(tick, pollMs);
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [running, currentId, onStatus, onError, pollMs]);
}
