// Shared Recharts styling for the dark theme.
export const chart = {
  grid: "#1f2a40",
  axis: "#94a3b8",
  cursor: "rgba(148, 163, 184, 0.08)",
  risk: { high: "#f87171", medium: "#fbbf24", low: "#34d399" },
  accent: "#818cf8",
  muted: "#64748b",
};

export const axisProps = {
  tick: { fill: chart.axis, fontSize: 12 },
  tickLine: false,
  axisLine: false,
};

export const tooltipProps = {
  cursor: { fill: chart.cursor },
  contentStyle: {
    background: "#111827",
    border: "1px solid #2c3a55",
    borderRadius: 8,
    color: "#e2e8f0",
    fontSize: 12,
  },
  labelStyle: { color: "#e2e8f0", fontWeight: 600 },
  itemStyle: { color: "#cbd5e1" },
};
