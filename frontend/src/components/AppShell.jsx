import { useEffect, useState } from "react";
import { BugIcon, CloseIcon, FilesIcon, LogoMark, LogoutIcon, MenuIcon, ModelIcon, OverviewIcon, PlayIcon } from "./icons.jsx";
import { Badge, Spinner } from "./ui.jsx";

export const NAV = [
  { id: "overview", label: "Overview", icon: OverviewIcon },
  { id: "files", label: "File risk explorer", icon: FilesIcon },
  { id: "predictions", label: "ML predictions", icon: ModelIcon },
  { id: "bug-fixes", label: "Bug-fix insights", icon: BugIcon },
  { id: "analyses", label: "Run analysis", icon: PlayIcon },
];

export default function AppShell({ route, onNavigate, user, onLogout, health, current, children }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const go = (id) => {
    onNavigate(id);
    setMenuOpen(false);
  };

  async function logout() {
    setSigningOut(true);
    await onLogout();
  }

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 px-5 py-5">
        <LogoMark />
        <div>
          <p className="font-semibold tracking-tight text-white">RepoRisk</p>
          <p className="text-xs text-slate-500">Git history risk analysis</p>
        </div>
      </div>
      <nav aria-label="Main" className="flex-1 space-y-1 px-3 py-2">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = route === id;
          return (
            <a
              key={id}
              href={`#/${id}`}
              onClick={(e) => {
                e.preventDefault();
                go(id);
              }}
              aria-current={active ? "page" : undefined}
              className={`group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                active ? "bg-indigo-500/15 text-white ring-1 ring-inset ring-indigo-400/25" : "text-slate-400 hover:bg-raised hover:text-slate-100"
              }`}
            >
              <Icon className={`h-[18px] w-[18px] ${active ? "text-indigo-300" : "text-slate-500 group-hover:text-slate-300"}`} />
              {label}
            </a>
          );
        })}
      </nav>
      {health && (
        <div className="px-5 pb-3 text-xs text-slate-500">
          <div className="flex flex-wrap gap-1.5">
            <Badge tone="success">API {health.version}</Badge>
            <Badge tone={health.ml_available ? "info" : "neutral"}
                   title={health.ml_available ? `scikit-learn ${health.scikit_learn_version}` : "scikit-learn is not installed"}>
              ML {health.ml_available ? "available" : "unavailable"}
            </Badge>
          </div>
        </div>
      )}
      <div className="border-t border-line px-3 py-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-1.5">
          <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-500/20 text-sm font-semibold uppercase text-indigo-200">
            {user.email[0]}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-slate-200" title={user.email}>{user.email}</p>
            <p className="text-xs text-slate-500">Signed in</p>
          </div>
          <button
            type="button"
            onClick={logout}
            disabled={signingOut}
            aria-label="Sign out"
            title="Sign out"
            className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-raised hover:text-white disabled:opacity-50"
          >
            {signingOut ? <Spinner className="h-4 w-4 text-slate-300" /> : <LogoutIcon className="h-[18px] w-[18px]" />}
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen lg:pl-64">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-indigo-500 focus:px-3 focus:py-2 focus:text-white">
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-line bg-surface lg:block">{sidebar}</aside>

      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMenuOpen(false)} aria-hidden="true" />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85%] border-r border-line bg-surface shadow-2xl">
            <button type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation"
                    className="absolute right-3 top-5 rounded-md p-1 text-slate-400 hover:bg-raised hover:text-white">
              <CloseIcon className="h-5 w-5" />
            </button>
            {sidebar}
          </div>
        </div>
      )}

      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-canvas/85 px-4 py-3 backdrop-blur sm:px-6">
        <button type="button" onClick={() => setMenuOpen(true)} aria-label="Open navigation"
                className="rounded-md p-1.5 text-slate-300 hover:bg-raised hover:text-white lg:hidden">
          <MenuIcon className="h-5 w-5" />
        </button>
        <div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
          <CurrentAnalysis current={current} onOpen={() => go("analyses")} />
        </div>
      </header>

      <main id="main" className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:py-8">{children}</main>
    </div>
  );
}

function CurrentAnalysis({ current, onOpen }) {
  if (!current) return <span className="text-slate-500">No analysis selected</span>;
  const running = current.status === "queued" || current.status === "running";
  return (
    <button type="button" onClick={onOpen}
            className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1 text-left transition-colors hover:bg-raised">
      <span className="truncate font-medium text-slate-100">{current.repository_name}</span>
      {running ? (
        <Badge tone="info">
          <Spinner className="h-3 w-3 text-indigo-200" />
          {current.stage_label ? `Step ${current.stage_index}/${current.stage_count}` : "Queued"}
        </Badge>
      ) : (
        <Badge tone={current.status === "completed" ? "success" : "danger"}>{current.status}</Badge>
      )}
    </button>
  );
}
