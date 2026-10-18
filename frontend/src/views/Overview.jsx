import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, Notice, StatCard } from "../components/ui.jsx";
import { OUTCOME_LABELS, formatDate, formatDateTime, formatNumber, shortHash } from "../lib/format.js";

const RISK_COLORS = { high: "#dc2626", medium: "#d97706", low: "#059669" };
const STATUS_TONES = { completed: "success", failed: "danger", running: "info", queued: "neutral" };

export default function Overview({ summary, analyses, currentId, onSelectAnalysis, onNavigate }) {
  const risk = summary.risk_counts;
  const riskData = ["high", "medium", "low"].map((level) => ({
    level: level[0].toUpperCase() + level.slice(1),
    key: level,
    files: risk[level] || 0,
  }));

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Files analyzed" value={formatNumber(summary.files_analyzed)}
                  hint={`current files of ${formatNumber(summary.files_touched_total)} ever touched`} />
        <StatCard label="High-risk files" value={formatNumber(risk.high)} tone="high" hint="3+ risk factors" />
        <StatCard label="Medium-risk files" value={formatNumber(risk.medium)} tone="medium" hint="1–2 risk factors" />
        <StatCard label="Low-risk files" value={formatNumber(risk.low)} tone="low" hint="no risk factors" />
      </div>

      {summary.warnings.length > 0 && (
        <Notice tone="warning" title="Warnings">
          <ul className="list-disc pl-5">
            {summary.warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </Notice>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Heuristic risk distribution" className="lg:col-span-2"
              description="Levels count transparent risk factors; they are not ML predictions.">
          <div className="h-56" aria-label="Bar chart of files per risk level" role="img">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={riskData} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="level" tickLine={false} axisLine={false} />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} />
                <Tooltip cursor={{ fill: "#f1f5f9" }} />
                <Bar dataKey="files" radius={[4, 4, 0, 0]}>
                  {riskData.map((d) => <Cell key={d.key} fill={RISK_COLORS[d.key]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <button type="button" onClick={() => onNavigate("files")}
                  className="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-500">
            Explore files and risk factors →
          </button>
        </Card>

        <Card title="Analysis">
          <dl className="space-y-2 text-sm">
            <Row label="Repository" value={summary.repository.name} />
            <Row label="HEAD" value={<code className="font-mono">{shortHash(summary.repository.head_commit)}</code>} />
            <Row label="Commits" value={formatNumber(summary.commits_analyzed)} />
            <Row label="History" value={`${formatDate(summary.observation_start)} – ${formatDate(summary.observation_end)}`} />
            <Row label="Completed" value={formatDateTime(summary.completed_at)} />
            <Row label="ML model" value={
              <Badge tone={summary.model.status === "available" ? "success" : "neutral"}>
                {summary.model.status === "available" ? "available" : "unavailable"}
              </Badge>
            } />
          </dl>
          {summary.model.reason && <p className="mt-2 text-xs text-slate-500">{summary.model.reason}</p>}
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Bug-fix detection" description="Heuristic classification of commit messages and changed files.">
          <dl className="grid grid-cols-3 gap-3 text-center">
            {Object.entries(summary.bug_fix_counts).map(([outcome, count]) => (
              <div key={outcome} className="rounded-lg bg-slate-50 px-2 py-3">
                <dt className="text-xs text-slate-500">{OUTCOME_LABELS[outcome] || outcome}</dt>
                <dd className="text-lg font-semibold tabular-nums">{formatNumber(count)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-sm text-slate-600">
            {summary.szz.status === "completed"
              ? `SZZ traced ${formatNumber(summary.szz.fixes_traced)} likely fixes to ${formatNumber(summary.szz.inducing_commits)} candidate bug-inducing commits.`
              : `SZZ tracing ${summary.szz.reason ? `was skipped: ${summary.szz.reason}` : "was skipped"}.`}
          </p>
          <button type="button" onClick={() => onNavigate("bugfixes")}
                  className="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-500">
            View bug-fix insights →
          </button>
        </Card>

        <Card title="How risk levels are defined">
          <ul className="space-y-1 text-sm">
            {summary.risk_level_definitions.map((d) => (
              <li key={d.code}><strong>{d.label}:</strong> {d.rule}</li>
            ))}
          </ul>
          <p className="mt-3 text-sm font-medium text-slate-700">Risk factors</p>
          <ul className="mt-1 space-y-1 text-sm text-slate-600">
            {summary.risk_factor_definitions.map((d) => (
              <li key={d.code}><strong className="text-slate-800">{d.label}</strong> — {d.rule}</li>
            ))}
          </ul>
        </Card>
      </div>

      {analyses.length > 0 && (
        <Card title="Recent analyses" description="Kept in server memory until the backend restarts.">
          <ul className="divide-y divide-slate-100 text-sm">
            {analyses.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <strong>{a.repository_name}</strong>{" "}
                  <span className="text-slate-500">{formatDateTime(a.created_at)}</span>
                </span>
                <span className="flex items-center gap-2">
                  <Badge tone={STATUS_TONES[a.status]}>{a.status}</Badge>
                  {a.status === "completed" && a.id !== currentId && (
                    <button type="button" onClick={() => onSelectAnalysis(a)}
                            className="text-indigo-600 hover:text-indigo-500">View</button>
                  )}
                  {a.id === currentId && <span className="text-xs text-slate-500">showing</span>}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium text-slate-800">{value}</dd>
    </div>
  );
}
