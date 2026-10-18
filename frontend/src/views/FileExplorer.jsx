import { Fragment, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { Badge, Card, EmptyState, ErrorState, LoadingState, Notice, PathText, RiskBadge, SortHeader, inputClass } from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate, formatNumber, formatPercent } from "../lib/format.js";

const RISK_ORDER = { high: 3, medium: 2, low: 1 };
const SORTERS = {
  path: (f) => f.path,
  risk: (f) => RISK_ORDER[f.risk_level] * 1e9 + f.risk_factors.length * 1e6 + f.churn,
  churn: (f) => f.churn,
  commits: (f) => f.commits,
  churn_90d: (f) => f.churn_90d,
  authors: (f) => f.authors,
  strongest_coupling: (f) => f.strongest_coupling,
  bug_fix_commits: (f) => f.bug_fix_commits,
};

export default function FileExplorer({ analysisId }) {
  const { data, error, loading, reload } = useApi(() => api.files(analysisId), [analysisId]);
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState("all");
  const [sort, setSort] = useState({ column: "risk", direction: "desc" });
  const [expanded, setExpanded] = useState(null);

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    const key = SORTERS[sort.column];
    const sign = sort.direction === "asc" ? 1 : -1;
    return data.items
      .filter((f) => (level === "all" || f.risk_level === level) && f.path.toLowerCase().includes(needle))
      .sort((a, b) => {
        const x = key(a), y = key(b);
        if (x === y) return a.path.localeCompare(b.path);
        return (x < y ? -1 : 1) * sign;
      });
  }, [data, search, level, sort]);

  const onSort = (column) =>
    setSort((s) => ({
      column,
      direction: s.column === column && s.direction === "desc" ? "asc" : column === "path" ? "asc" : "desc",
    }));

  if (loading) return <LoadingState label="Loading files…" />;
  if (error) return <ErrorState error={error} onRetry={reload} title="Could not load files" />;

  return (
    <Card
      title="File risk explorer"
      description={`${formatNumber(data.total)} current files. Risk levels are heuristic indicators from git history; ML probabilities are on the ML predictions tab.`}
    >
      <div className="mb-4 flex flex-col gap-3 sm:flex-row">
        <div className="flex-1">
          <label htmlFor="file-search" className="sr-only">Search files</label>
          <input id="file-search" type="search" placeholder="Search by path…" className={inputClass}
                 value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="sm:w-48">
          <label htmlFor="risk-filter" className="sr-only">Filter by risk level</label>
          <select id="risk-filter" className={inputClass} value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="all">All risk levels</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
      </div>

      {data.items.length === 0 ? (
        <EmptyState title="No files to show">
          The analysis found no current files that match its filters. Try disabling “Source-code files only”
          or “Exclude test files” in the analysis options.
        </EmptyState>
      ) : rows.length === 0 ? (
        <EmptyState title="No matching files">Nothing matches “{search}”{level !== "all" ? ` at ${level} risk` : ""}.</EmptyState>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="border-y border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <SortHeader label="File" column="path" sort={sort} onSort={onSort} />
                <SortHeader label="Risk" column="risk" sort={sort} onSort={onSort} />
                <SortHeader label="Churn" column="churn" sort={sort} onSort={onSort} align="right" />
                <SortHeader label="Commits" column="commits" sort={sort} onSort={onSort} align="right" />
                <SortHeader label="90-day churn" column="churn_90d" sort={sort} onSort={onSort} align="right" />
                <SortHeader label="Authors" column="authors" sort={sort} onSort={onSort} align="right" />
                <SortHeader label="Coupling" column="strongest_coupling" sort={sort} onSort={onSort} align="right" />
                <SortHeader label="Bug-fix commits" column="bug_fix_commits" sort={sort} onSort={onSort} align="right" />
                <th scope="col" className="px-3 py-2 text-left font-medium">Risk factors</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((f) => (
                <Fragment key={f.path}>
                  <tr className="hover:bg-slate-50">
                    <td className="max-w-xs px-3 py-2">
                      <button type="button" aria-label={f.path} aria-expanded={expanded === f.path}
                              onClick={() => setExpanded(expanded === f.path ? null : f.path)}
                              className="text-left font-mono text-xs text-slate-800 hover:text-indigo-600">
                        <PathText path={f.path} />
                      </button>
                    </td>
                    <td className="px-3 py-2"><RiskBadge level={f.risk_level} /></td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatNumber(f.churn)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatNumber(f.commits)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatNumber(f.churn_90d)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatNumber(f.authors)}</td>
                    <td className="px-3 py-2 text-right tabular-nums" title={f.top_partner ? `with ${f.top_partner}` : undefined}>
                      {f.top_partner ? formatPercent(f.strongest_coupling) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatNumber(f.bug_fix_commits)}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {f.risk_factors.length === 0
                          ? <span className="text-slate-400">none</span>
                          : f.risk_factors.map((r) => <Badge key={r.code} tone={f.risk_level} title={r.detail}>{r.label}</Badge>)}
                      </div>
                    </td>
                  </tr>
                  {expanded === f.path && (
                    <tr className="bg-slate-50">
                      <td colSpan={9} className="px-5 py-3 text-sm text-slate-700">
                        <FileDetails file={f} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-4">
        <Notice>
          Click a file path to see why it was flagged. Coupling is the share of the file’s commits that also changed
          its most frequent partner.
        </Notice>
      </div>
    </Card>
  );
}

function FileDetails({ file }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <p className="font-semibold">Why this file is {file.risk_level} risk</p>
        {file.risk_factors.length === 0 ? (
          <p className="mt-1 text-slate-500">It meets none of the risk-factor rules.</p>
        ) : (
          <ul className="mt-1 list-disc pl-5">
            {file.risk_factors.map((r) => <li key={r.code}><strong>{r.label}:</strong> {r.detail}</li>)}
          </ul>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
        <dt className="text-slate-500">Lines added / deleted</dt>
        <dd>{formatNumber(file.lines_added)} / {formatNumber(file.lines_deleted)}</dd>
        <dt className="text-slate-500">Churn 30 / 90 / 180 days</dt>
        <dd>{formatNumber(file.churn_30d)} / {formatNumber(file.churn_90d)} / {formatNumber(file.churn_180d)}</dd>
        <dt className="text-slate-500">Author concentration (HHI)</dt>
        <dd>{file.author_hhi === null ? "—" : file.author_hhi.toFixed(2)}</dd>
        <dt className="text-slate-500">Coupled files</dt>
        <dd>{formatNumber(file.coupled_files)}{file.top_partner ? ` (top: ${file.top_partner})` : ""}</dd>
        <dt className="text-slate-500">First seen / last changed</dt>
        <dd>{formatDate(file.first_seen)} / {formatDate(file.last_changed)}</dd>
      </dl>
    </div>
  );
}
