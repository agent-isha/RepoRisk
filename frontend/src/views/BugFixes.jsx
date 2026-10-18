import { useMemo, useState } from "react";
import { api } from "../api/client.js";
import { Badge, Card, EmptyState, ErrorState, LoadingState, Notice, inputClass } from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { OUTCOME_LABELS, formatDate, formatNumber, shortHash } from "../lib/format.js";

const PAGE = 25;
const OUTCOME_TONES = { likely_bug_fix: "danger", uncertain: "warning" };

export default function BugFixes({ analysisId }) {
  const { data, error, loading, reload } = useApi(() => api.bugFixes(analysisId), [analysisId]);
  const [outcome, setOutcome] = useState("likely_bug_fix");
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState(PAGE);

  const items = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    return data.items.filter(
      (b) =>
        (outcome === "all" || b.outcome === outcome) &&
        (!needle || b.subject.toLowerCase().includes(needle) || b.hash.startsWith(needle)),
    );
  }, [data, outcome, search]);

  if (loading) return <LoadingState label="Loading bug-fix candidates…" />;
  if (error) return <ErrorState error={error} onRetry={reload} title="Could not load bug-fix candidates" />;

  return (
    <div className="space-y-6">
      <Notice tone="warning" title="Candidates, not confirmed bugs">
        <ul className="list-disc pl-5">{data.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </Notice>

      <Card
        title="Bug-fix insights"
        description={`${formatNumber(data.outcome_counts.likely_bug_fix)} likely and ${formatNumber(data.outcome_counts.uncertain)} uncertain bug-fix commits. ${
          data.szz.status === "completed"
            ? `SZZ found ${formatNumber(data.szz.inducing_commits)} candidate bug-inducing commits.`
            : "SZZ tracing was skipped for this analysis."
        }`}
      >
        <div className="mb-4 flex flex-col gap-3 sm:flex-row">
          <div className="flex-1">
            <label htmlFor="fix-search" className="sr-only">Search commits</label>
            <input id="fix-search" type="search" placeholder="Search by message or hash…" className={inputClass}
                   value={search} onChange={(e) => { setSearch(e.target.value); setShown(PAGE); }} />
          </div>
          <div className="sm:w-56">
            <label htmlFor="outcome-filter" className="sr-only">Filter by outcome</label>
            <select id="outcome-filter" className={inputClass} value={outcome}
                    onChange={(e) => { setOutcome(e.target.value); setShown(PAGE); }}>
              <option value="likely_bug_fix">Likely bug fixes</option>
              <option value="uncertain">Uncertain</option>
              <option value="all">All candidates</option>
            </select>
          </div>
        </div>

        {items.length === 0 ? (
          <EmptyState title="No bug-fix candidates">
            {data.items.length === 0
              ? "The detector found no likely or uncertain bug-fix commits in this repository."
              : "No candidates match the current filters."}
          </EmptyState>
        ) : (
          <ul className="space-y-3">
            {items.slice(0, shown).map((b) => <Candidate key={b.hash} fix={b} />)}
          </ul>
        )}
        {items.length > shown && (
          <button type="button" onClick={() => setShown((n) => n + PAGE)}
                  className="mt-4 text-sm font-medium text-indigo-600 hover:text-indigo-500">
            Show more ({formatNumber(items.length - shown)} remaining)
          </button>
        )}
      </Card>
    </div>
  );
}

function Candidate({ fix }) {
  return (
    <li className="rounded-lg border border-slate-200">
      <details>
        <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
          <code className="font-mono text-xs text-slate-500">{shortHash(fix.hash)}</code>
          <span className="flex-1 font-medium text-slate-800">{fix.subject || "(empty message)"}</span>
          <Badge tone={OUTCOME_TONES[fix.outcome]}>{OUTCOME_LABELS[fix.outcome]}</Badge>
          <span className="text-xs text-slate-500">{formatDate(fix.timestamp)}</span>
          <span className="text-xs tabular-nums">
            <span className="text-emerald-700">+{fix.added}</span> <span className="text-red-700">−{fix.deleted}</span>
          </span>
          {fix.szz_records.length > 0 && <Badge tone="info">{fix.szz_records.length} SZZ candidate{fix.szz_records.length > 1 ? "s" : ""}</Badge>}
        </summary>
        <div className="space-y-3 border-t border-slate-100 px-4 py-3 text-sm">
          <div>
            <p className="font-semibold text-slate-700">Classification evidence</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {fix.evidence.map((e) => <Badge key={e}>{e}</Badge>)}
            </div>
          </div>
          <div>
            <p className="font-semibold text-slate-700">Changed files</p>
            <ul className="mt-1 font-mono text-xs text-slate-600">
              {fix.files.map((f) => <li key={f.path}>{f.path} (+{f.added}/−{f.deleted})</li>)}
            </ul>
          </div>
          <div>
            <p className="font-semibold text-slate-700">SZZ candidate bug-inducing commits</p>
            {fix.szz_note && <p className="mt-1 text-slate-500">{fix.szz_note}</p>}
            {fix.szz_records.length === 0 ? (
              !fix.szz_note && <p className="mt-1 text-slate-500">None found.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {fix.szz_records.map((r) => (
                  <li key={`${r.inducing_hash}-${r.path}`} className="rounded-md bg-slate-50 px-3 py-2">
                    <p>
                      <code className="font-mono text-xs">{shortHash(r.inducing_hash)}</code>{" "}
                      <span className="text-slate-500">{formatDate(r.inducing_date)}</span>{" "}
                      <span className="font-mono text-xs">{r.path}</span>
                    </p>
                    <pre className="mt-1 overflow-x-auto rounded bg-white p-2 font-mono text-xs text-slate-700 ring-1 ring-slate-200">
                      {r.lines.map((l) => `${String(l.fix_parent_line).padStart(5)}  ${l.content}`).join("\n")}
                    </pre>
                    <p className="mt-1 text-xs text-slate-600">{r.evidence.join(" · ")}</p>
                    <p className="mt-1 text-xs text-amber-800">Limitations: {r.limitations.join("; ")}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {fix.message.trim() !== fix.subject && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">Full commit message</summary>
              <pre className="mt-1 whitespace-pre-wrap font-mono">{fix.message}</pre>
            </details>
          )}
        </div>
      </details>
    </li>
  );
}
