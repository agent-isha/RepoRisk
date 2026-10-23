import { useMemo, useState } from "react";
import { api } from "../api/client.js";
import { BugIcon, ChevronIcon, SearchIcon } from "../components/icons.jsx";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, Notice, PageHeader, inputClass } from "../components/ui.jsx";
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

  const header = (
    <PageHeader title="Bug-fix insights"
                description="Commits classified as bug fixes, and the earlier commits SZZ links them to." />
  );
  if (loading) return <>{header}<LoadingState label="Loading bug-fix candidates…" /></>;
  if (error) return <>{header}<ErrorState error={error} onRetry={reload} title="Could not load bug-fix candidates" /></>;

  return (
    <>
      {header}
      <div className="space-y-6">
        <Notice tone="warning" title="Candidates, not confirmed bugs">
          <ul className="list-disc pl-5">{data.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </Notice>

        <Card
          title={`${formatNumber(data.outcome_counts.likely_bug_fix)} likely and ${formatNumber(data.outcome_counts.uncertain)} uncertain bug-fix commits`}
          description={data.szz.status === "completed"
            ? `SZZ found ${formatNumber(data.szz.inducing_commits)} candidate bug-inducing commits.`
            : "SZZ tracing was skipped for this analysis."}
        >
          <div className="mb-4 flex flex-col gap-3 sm:flex-row">
            <div className="relative flex-1">
              <label htmlFor="fix-search" className="sr-only">Search commits</label>
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
              <input id="fix-search" type="search" placeholder="Search by message or hash…" className={`${inputClass} pl-9`}
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
            <EmptyState icon={<BugIcon className="h-5 w-5" />} title="No bug-fix candidates">
              {data.items.length === 0
                ? "The detector found no likely or uncertain bug-fix commits in this repository."
                : "No candidates match the current filters."}
            </EmptyState>
          ) : (
            <ul className="space-y-2">
              {items.slice(0, shown).map((b) => <Candidate key={b.hash} fix={b} />)}
            </ul>
          )}
          {items.length > shown && (
            <Button variant="secondary" className="mt-4" onClick={() => setShown((n) => n + PAGE)}>
              Show more ({formatNumber(items.length - shown)} remaining)
            </Button>
          )}
        </Card>
      </div>
    </>
  );
}

function Candidate({ fix }) {
  return (
    <li className="rounded-lg border border-line bg-raised/30 transition-colors hover:border-line-strong">
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg px-4 py-3 [&::-webkit-details-marker]:hidden">
          <ChevronIcon className="h-4 w-4 shrink-0 text-slate-500 transition-transform group-open:rotate-90" />
          <code className="font-mono text-xs text-slate-500">{shortHash(fix.hash)}</code>
          <span className="min-w-0 flex-1 font-medium text-slate-100">{fix.subject || "(empty message)"}</span>
          <Badge tone={OUTCOME_TONES[fix.outcome]}>{OUTCOME_LABELS[fix.outcome]}</Badge>
          <span className="text-xs text-slate-500">{formatDate(fix.timestamp)}</span>
          <span className="text-xs tabular-nums">
            <span className="text-emerald-400">+{fix.added}</span> <span className="text-red-400">−{fix.deleted}</span>
          </span>
          {fix.szz_records.length > 0 && (
            <Badge tone="info">{fix.szz_records.length} SZZ candidate{fix.szz_records.length > 1 ? "s" : ""}</Badge>
          )}
        </summary>
        <div className="space-y-4 border-t border-line px-4 py-4 text-sm">
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">Classification evidence</h3>
            <div className="mt-2 flex flex-wrap gap-1.5">{fix.evidence.map((e) => <Badge key={e}>{e}</Badge>)}</div>
          </section>
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">Changed files</h3>
            <ul className="mt-2 space-y-0.5 font-mono text-xs text-slate-300">
              {fix.files.map((f) => <li key={f.path}>{f.path} <span className="text-slate-500">(+{f.added}/−{f.deleted})</span></li>)}
            </ul>
          </section>
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">SZZ candidate bug-inducing commits</h3>
            {fix.szz_note && <p className="mt-2 text-slate-400">{fix.szz_note}</p>}
            {fix.szz_records.length === 0 ? (
              !fix.szz_note && <p className="mt-2 text-slate-400">None found.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {fix.szz_records.map((r) => (
                  <li key={`${r.inducing_hash}-${r.path}`} className="rounded-lg border border-line bg-canvas/60 p-3">
                    <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <code className="font-mono text-xs text-indigo-300">{shortHash(r.inducing_hash)}</code>
                      <span className="text-xs text-slate-500">{formatDate(r.inducing_date)}</span>
                      <span className="font-mono text-xs text-slate-300">{r.path}</span>
                    </p>
                    <pre className="mt-2 overflow-x-auto rounded-md border border-line bg-black/30 p-2.5 font-mono text-xs leading-relaxed text-slate-300">
                      {r.lines.map((l) => `${String(l.fix_parent_line).padStart(5)}  ${l.content}`).join("\n")}
                    </pre>
                    <p className="mt-2 text-xs text-slate-400">{r.evidence.join(" · ")}</p>
                    <p className="mt-1 text-xs text-amber-200/80">Limitations: {r.limitations.join("; ")}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {fix.message.trim() !== fix.subject && (
            <details className="text-xs text-slate-400">
              <summary className="cursor-pointer text-slate-300">Full commit message</summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono">{fix.message}</pre>
            </details>
          )}
        </div>
      </details>
    </li>
  );
}
