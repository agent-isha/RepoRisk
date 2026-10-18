export const formatNumber = (value) =>
  value === null || value === undefined ? "—" : Number(value).toLocaleString("en-US");

export const formatPercent = (value, digits = 0) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

// Metrics can legitimately be undefined (e.g. recall with no positives); say so.
export const formatMetric = (value, digits = 3) =>
  value === null || value === undefined ? "undefined" : Number(value).toFixed(digits);

export const formatDate = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
};

export const formatDateTime = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
};

export const shortHash = (hash) => (hash ? hash.slice(0, 8) : "—");

export const OUTCOME_LABELS = {
  likely_bug_fix: "Likely bug fix",
  uncertain: "Uncertain",
  not_likely_bug_fix: "Not likely",
};
