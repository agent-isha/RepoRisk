import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChevronIcon } from "../components/icons.jsx";
import { Badge, Card, Notice, PageHeader, StatCard, linkButtonClass } from "../components/ui.jsx";
import { axisProps, chart, tooltipProps } from "../lib/chart.js";
import { OUTCOME_LABELS, formatDate, formatDateTime, formatNumber, shortHash } from "../lib/format.js";

export default function Overview({ summary, onNavigate }) {
  const risk = summary.risk_counts;
  const riskData = ["high", "medium", "low"].map((level) => ({
    level: level[0].toUpperCase() + level.slice(1),
    key: level,
    files: risk[level] || 0,
  }));

  return (
    <>
      <PageHeader
        title="Overview"
        description={`${summary.repository.name} · ${formatNumber(summary.commits_analyzed)} commits · ${formatDate(summary.observation_start)} – ${formatDate(summary.observation_end)}`}
      />
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Files analyzed" value={formatNumber(summary.files_analyzed)}
                    hint={`current files of ${formatNumber(summary.files_touched_total)} ever touched`} />
          <StatCard label="High-risk files" value={formatNumber(risk.high)} tone="high" hint="3+ risk factors" />
          <StatCard label="Medium-risk files" value={formatNumber(risk.medium)} tone="medium" hint="1–2 risk factors" />
          <StatCard label="Low-risk files" value={formatNumber(risk.low)} tone="low" hint="no risk factors" />
        </div>

        {summary.warnings.length > 0 && (
          <Notice tone="warning" title="Warnings">
            <ul className="list-disc pl-5">{summary.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          </Notice>
        )}

        <div className="grid gap-6 lg:grid-cols-3">
          <Card title="Heuristic risk distribution" className="lg:col-span-2"
                description="Levels count transparent risk factors; they are not ML predictions.">
            <div className="h-60" role="img" aria-label={`Files per risk level: ${riskData.map((d) => `${d.level} ${d.files}`).join(", ")}`}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={riskData} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={chart.grid} />
                  <XAxis dataKey="level" {...axisProps} />
                  <YAxis allowDecimals={false} {...axisProps} />
                  <Tooltip {...tooltipProps} />
                  <Bar dataKey="files" name="Files" radius={[6, 6, 0, 0]} maxBarSize={72}>
                    {riskData.map((d) => <Cell key={d.key} fill={chart.risk[d.key]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <button type="button" onClick={() => onNavigate("files")} className={`mt-3 ${linkButtonClass}`}>
              Explore files and risk factors <ChevronIcon className="h-4 w-4" />
            </button>
          </Card>

          <Card title="Analysis">
            <dl className="space-y-3 text-sm">
              <Row label="Repository" value={summary.repository.name} />
              <Row label="HEAD" value={<code className="font-mono text-slate-300">{shortHash(summary.repository.head_commit)}</code>} />
              <Row label="Commits" value={formatNumber(summary.commits_analyzed)} />
              <Row label="Completed" value={formatDateTime(summary.completed_at)} />
              <Row label="ML model" value={
                <Badge tone={summary.model.status === "available" ? "success" : "neutral"}>{summary.model.status}</Badge>
              } />
            </dl>
            {summary.model.reason && <p className="mt-3 text-xs text-slate-500">{summary.model.reason}</p>}
            <button type="button" onClick={() => onNavigate("predictions")} className={`mt-3 ${linkButtonClass}`}>
              Model predictions and evaluation <ChevronIcon className="h-4 w-4" />
            </button>
          </Card>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Bug-fix detection" description="Heuristic classification of commit messages and changed files.">
            <dl className="grid grid-cols-3 gap-3 text-center">
              {Object.entries(summary.bug_fix_counts).map(([outcome, count]) => (
                <div key={outcome} className="rounded-lg border border-line bg-raised/50 px-2 py-3">
                  <dt className="text-xs text-slate-400">{OUTCOME_LABELS[outcome] || outcome}</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums text-white">{formatNumber(count)}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-sm text-slate-400">
              {summary.szz.status === "completed"
                ? `SZZ traced ${formatNumber(summary.szz.fixes_traced)} likely fixes to ${formatNumber(summary.szz.inducing_commits)} candidate bug-inducing commits.`
                : `SZZ tracing ${summary.szz.reason ? `was skipped: ${summary.szz.reason}` : "was skipped"}.`}
            </p>
            <button type="button" onClick={() => onNavigate("bug-fixes")} className={`mt-3 ${linkButtonClass}`}>
              View bug-fix insights <ChevronIcon className="h-4 w-4" />
            </button>
          </Card>

          <Card title="How risk levels are defined">
            <ul className="space-y-1.5 text-sm text-slate-300">
              {summary.risk_level_definitions.map((d) => (
                <li key={d.code}><strong className="text-white">{d.label}:</strong> {d.rule}</li>
              ))}
            </ul>
            <p className="mt-4 text-xs font-medium uppercase tracking-wide text-slate-400">Risk factors</p>
            <ul className="mt-2 space-y-1.5 text-sm text-slate-400">
              {summary.risk_factor_definitions.map((d) => (
                <li key={d.code}><strong className="font-medium text-slate-200">{d.label}</strong> — {d.rule}</li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right font-medium text-slate-100">{value}</dd>
    </div>
  );
}
