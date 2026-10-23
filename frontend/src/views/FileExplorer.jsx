import { useMemo, useState } from "react";
import { api } from "../api/client.js";
import { SearchIcon } from "../components/icons.jsx";
import {
  Badge, Card, EmptyState, ErrorState, LoadingState, Modal, Notice, PageHeader, PathText, RiskBadge, SortHeader,
  inputClass, rowClass, tableClass, theadClass,
} from "../components/ui.jsx";
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
  const [selected, setSelected] = useState(null);

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

  const header = (
    <PageHeader title="File risk explorer"
                description="Heuristic risk indicators from git history. ML probabilities are on the ML predictions page." />
  );
  if (loading) return <>{header}<LoadingState label="Loading files…" /></>;
  if (error) return <>{header}<ErrorState error={error} onRetry={reload} title="Could not load files" /></>;

  return (
    <>
      {header}
      <Card bodyClassName="p-0">
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <label htmlFor="file-search" className="sr-only">Search files</label>
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input id="file-search" type="search" placeholder="Search by path…" className={`${inputClass} pl-9`}
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
          <p className="text-xs text-slate-500 sm:ml-2" aria-live="polite">
            {formatNumber(rows.length)} of {formatNumber(data.total)} files
          </p>
        </div>

        {data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No files to show">
              The analysis found no current files that match its filters. Try disabling “Source-code files only” or
              “Exclude test files” in the analysis options.
            </EmptyState>
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No matching files">Nothing matches “{search}”{level !== "all" ? ` at ${level} risk` : ""}.</EmptyState>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className={tableClass}>
              <thead className={theadClass}>
                <tr>
                  <SortHeader label="File" column="path" sort={sort} onSort={onSort} />
                  <SortHeader label="Risk" column="risk" sort={sort} onSort={onSort} />
                  <SortHeader label="Churn" column="churn" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Commits" column="commits" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="90-day churn" column="churn_90d" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Authors" column="authors" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Coupling" column="strongest_coupling" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Bug-fix commits" column="bug_fix_commits" sort={sort} onSort={onSort} align="right" />
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">Risk factors</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => (
                  <tr key={f.path} className={rowClass}>
                    <td className="max-w-xs px-3 py-2.5">
                      <button type="button" aria-label={f.path} aria-haspopup="dialog" onClick={() => setSelected(f)}
                              className="text-left font-mono text-xs text-slate-200 transition-colors hover:text-indigo-300">
                        <PathText path={f.path} />
                      </button>
                    </td>
                    <td className="px-3 py-2.5"><RiskBadge level={f.risk_level} /></td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">{formatNumber(f.churn)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">{formatNumber(f.commits)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">{formatNumber(f.churn_90d)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">{formatNumber(f.authors)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-300" title={f.top_partner ? `with ${f.top_partner}` : undefined}>
                      {f.top_partner ? formatPercent(f.strongest_coupling) : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">{formatNumber(f.bug_fix_commits)}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {f.risk_factors.length === 0
                          ? <span className="text-slate-600">none</span>
                          : f.risk_factors.map((r) => <Badge key={r.code} tone={f.risk_level} title={r.detail}>{r.label}</Badge>)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="mt-4">
        <Notice>Select a file path to see why it was flagged. Coupling is the share of the file’s commits that also changed its most frequent partner.</Notice>
      </div>
      <Modal open={Boolean(selected)} title={selected?.path} onClose={() => setSelected(null)}>
        {selected && <FileDetails file={selected} />}
      </Modal>
    </>
  );
}

function FileDetails({ file }) {
  return (
    <div className="space-y-5 text-sm">
      <div>
        <div className="flex items-center gap-2">
          <RiskBadge level={file.risk_level} />
          <p className="font-semibold text-slate-100">Why this file is {file.risk_level} risk</p>
        </div>
        {file.risk_factors.length === 0 ? (
          <p className="mt-2 text-slate-400">It meets none of the risk-factor rules.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {file.risk_factors.map((r) => (
              <li key={r.code} className="rounded-lg border border-line bg-raised/50 px-3 py-2">
                <p className="font-medium text-slate-100">{r.label}</p>
                <p className="mt-0.5 text-slate-400">{r.detail}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        {[
          ["Lines added / deleted", `${formatNumber(file.lines_added)} / ${formatNumber(file.lines_deleted)}`],
          ["Churn 30 / 90 / 180 days", `${formatNumber(file.churn_30d)} / ${formatNumber(file.churn_90d)} / ${formatNumber(file.churn_180d)}`],
          ["Commits / authors", `${formatNumber(file.commits)} / ${formatNumber(file.authors)}`],
          ["Author concentration (HHI)", file.author_hhi === null ? "—" : file.author_hhi.toFixed(2)],
          ["Coupled files", `${formatNumber(file.coupled_files)}${file.top_partner ? ` (top: ${file.top_partner})` : ""}`],
          ["Likely bug-fix commits", formatNumber(file.bug_fix_commits)],
          ["First seen", formatDate(file.first_seen)],
          ["Last changed", formatDate(file.last_changed)],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 border-b border-line/60 py-1.5 sm:block sm:border-0 sm:py-0">
            <dt className="text-slate-500">{label}</dt>
            <dd className="break-words text-slate-200 sm:mt-0.5">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
