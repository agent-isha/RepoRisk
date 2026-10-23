import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client.js";
import { ModelIcon } from "../components/icons.jsx";
import {
  Badge, Card, EmptyState, ErrorState, LoadingState, Notice, PageHeader, PathText, RiskBadge,
  rowClass, tableClass, theadClass,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { axisProps, chart, tooltipProps } from "../lib/chart.js";
import { formatDate, formatMetric, formatNumber, formatPercent } from "../lib/format.js";

const MODEL_NAMES = { churn_baseline: "Churn baseline", logistic_regression: "Logistic regression" };

export default function Predictions({ analysisId }) {
  const { data, error, loading, reload } = useApi(() => api.predictions(analysisId), [analysisId]);
  const header = (
    <PageHeader title="ML predictions"
                description="Probability that a commit classified as a likely bug fix touches each file within the horizon." />
  );

  if (loading) return <>{header}<LoadingState label="Loading predictions…" /></>;
  if (error) return <>{header}<ErrorState error={error} onRetry={reload} title="Could not load predictions" /></>;

  return (
    <>
      {header}
      <div className="space-y-6">
        <Notice tone="warning" title="Heuristic labels, not verified defects">{data.label_note}</Notice>

        {data.status === "available" ? <PredictionTable data={data} /> : (
          <Card title="Model predictions">
            <EmptyState icon={<ModelIcon className="h-5 w-5" />} title="No model predictions for this analysis">
              <p>{data.reason}</p>
              <p className="mt-2">
                Predictions appear only when a model has actually been trained. Install the optional ML dependencies
                (<code className="font-mono text-slate-300">pip install -r requirements-ml.txt</code>), enable “Build
                dataset and train ML model”, and make sure the repository has enough history for at least two complete
                snapshots.
              </p>
            </EmptyState>
          </Card>
        )}

        <EvaluationCard evaluation={data.evaluation} unavailableReason={data.evaluation_unavailable_reason} />

        <Card title="Limitations">
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-400">
            {data.limitations.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </Card>
      </div>
    </>
  );
}

function PredictionTable({ data }) {
  return (
    <Card
      title="Predicted probability of a future bug-fix commit"
      description={`Scores for the ${formatDate(data.scored_snapshot)} snapshot over the next ${data.horizon_days} days. Model trained on ${formatNumber(data.trained_on_rows)} labelled rows (${formatNumber(data.trained_on_positives)} positive).`}
      bodyClassName="p-0"
    >
      {data.predictions.length === 0 ? (
        <div className="p-4"><EmptyState title="The latest snapshot has no files to score" /></div>
      ) : (
        <div className="overflow-x-auto">
          <table className={tableClass}>
            <thead className={theadClass}>
              <tr>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Rank</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">File</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Probability</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Heuristic level</th>
              </tr>
            </thead>
            <tbody>
              {data.predictions.map((p) => (
                <tr key={p.path} className={rowClass}>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-500">{p.rank}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-200"><PathText path={p.path} /></td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-raised" aria-hidden="true">
                        <div className="h-full rounded-full bg-indigo-400" style={{ width: `${p.probability * 100}%` }} />
                      </div>
                      <span className="tabular-nums text-slate-200">{formatPercent(p.probability, 1)}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5"><RiskBadge level={p.heuristic_risk_level} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function EvaluationCard({ evaluation, unavailableReason }) {
  if (!evaluation) {
    return (
      <Card title="Model evaluation">
        <EmptyState title="No evaluation available">{unavailableReason || "The dataset was not built for this analysis."}</EmptyState>
      </Card>
    );
  }
  const trained = evaluation.models.filter((m) => m.status === "trained");
  const ks = trained[0] ? Object.keys(trained[0].precision_at_k) : [];
  const chartData = trained.map((m) => ({ name: MODEL_NAMES[m.name] || m.name, pr_auc: m.pr_auc ?? 0 }));

  return (
    <Card
      title="Model evaluation (chronological hold-out)"
      description={`Trained on snapshots before ${formatDate(evaluation.test_start)}, tested on later ones: ${formatNumber(evaluation.test_rows)} test rows with ${formatNumber(evaluation.test_positives)} positives (prevalence ${formatPercent(evaluation.test_prevalence, 1)}).`}
      bodyClassName="p-0"
    >
      {evaluation.warnings.length > 0 && (
        <div className="p-4 pb-0">
          <Notice tone="warning" title="Evaluation warnings">
            <ul className="list-disc pl-5">{evaluation.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          </Notice>
        </div>
      )}
      <div className="mt-4 overflow-x-auto">
        <table className={tableClass}>
          <thead className={theadClass}>
            <tr>
              <th scope="col" className="px-3 py-2.5 text-left font-medium">Model</th>
              {["Precision", "Recall", "F1", "PR-AUC", "ROC-AUC", ...ks.map((k) => `P@${k}`)].map((h) => (
                <th key={h} scope="col" className="px-3 py-2.5 text-right font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {evaluation.models.map((m) => (
              <tr key={m.name} className={rowClass}>
                <th scope="row" className="px-3 py-2.5 text-left font-medium text-slate-100">
                  {MODEL_NAMES[m.name] || m.name}
                  {m.status === "skipped" && <span className="ml-2"><Badge>skipped</Badge></span>}
                </th>
                {m.status === "skipped" ? (
                  <td colSpan={5 + ks.length} className="px-3 py-2.5 text-slate-400">{m.reason}</td>
                ) : (
                  [m.precision, m.recall, m.f1, m.pr_auc, m.roc_auc, ...ks.map((k) => m.precision_at_k[k])].map((v, i) => (
                    <td key={i} className={`px-3 py-2.5 text-right tabular-nums ${v === null ? "text-slate-500" : "text-slate-200"}`}>
                      {formatMetric(v)}
                    </td>
                  ))
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {chartData.length > 0 && (
        <div className="h-44 px-4 pt-4" role="img"
             aria-label={`PR-AUC by model: ${chartData.map((d) => `${d.name} ${d.pr_auc.toFixed(3)}`).join(", ")}; random ranking scores ${formatMetric(evaluation.test_prevalence)}`}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 24 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke={chart.grid} />
              <XAxis type="number" domain={[0, 1]} {...axisProps} />
              <YAxis type="category" dataKey="name" width={140} {...axisProps} />
              <Tooltip {...tooltipProps} formatter={(v) => Number(v).toFixed(3)} />
              <ReferenceLine x={evaluation.test_prevalence ?? 0} stroke={chart.muted} strokeDasharray="4 4"
                             label={{ value: "random", position: "top", fill: chart.axis, fontSize: 11 }} />
              <Bar dataKey="pr_auc" name="PR-AUC" fill={chart.accent} radius={[0, 6, 6, 0]} maxBarSize={28} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="px-5 py-4 text-xs text-slate-500">
        Both models flag the same number of test rows. “undefined” means the metric cannot be computed for this split (for
        example, recall with no positive rows). Compare PR-AUC with the dashed prevalence line, which is what random
        ranking would score.
      </p>
    </Card>
  );
}
