// RepoRisk design system: shared, presentational building blocks for every view.
import { useEffect, useId, useRef } from "react";
import { AlertIcon, CloseIcon, InfoIcon } from "./icons.jsx";

// ---------------------------------------------------------------- surfaces

export function Card({ title, description, actions, children, className = "", bodyClassName = "px-5 py-4" }) {
  return (
    <section className={`rounded-xl border border-line bg-surface shadow-card ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-slate-100">{title}</h2>}
            {description && <p className="mt-1 text-sm text-slate-400">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-white">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate-400">{description}</p>}
      </div>
      {actions}
    </div>
  );
}

const STAT_ACCENTS = {
  high: "text-red-400",
  medium: "text-amber-300",
  low: "text-emerald-400",
  accent: "text-indigo-300",
};

export function StatCard({ label, value, hint, tone }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-5 py-4 shadow-card transition-colors hover:border-line-strong">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${STAT_ACCENTS[tone] || "text-white"}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- badges

const TONES = {
  neutral: "bg-slate-400/10 text-slate-300 ring-slate-400/20",
  high: "bg-red-500/10 text-red-300 ring-red-500/25",
  medium: "bg-amber-400/10 text-amber-200 ring-amber-400/25",
  low: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
  info: "bg-indigo-500/10 text-indigo-200 ring-indigo-400/30",
  success: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
  danger: "bg-red-500/10 text-red-300 ring-red-500/25",
  warning: "bg-amber-400/10 text-amber-200 ring-amber-400/25",
};

export function Badge({ tone = "neutral", title, children }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone] || TONES.neutral}`}
    >
      {children}
    </span>
  );
}

const RISK_LABELS = { high: "High", medium: "Medium", low: "Low" };

export function RiskBadge({ level }) {
  if (!level) return <span className="text-slate-500">—</span>;
  return <Badge tone={level}>{RISK_LABELS[level] || level}</Badge>;
}

// ---------------------------------------------------------------- buttons and inputs

const BUTTON_VARIANTS = {
  primary:
    "bg-indigo-500 text-white shadow-sm hover:bg-indigo-400 disabled:bg-indigo-500/40 disabled:text-white/60",
  secondary:
    "bg-raised text-slate-200 ring-1 ring-inset ring-line-strong hover:bg-line hover:text-white disabled:opacity-50",
  ghost: "text-slate-300 hover:bg-raised hover:text-white disabled:opacity-50",
  danger: "bg-red-500/10 text-red-300 ring-1 ring-inset ring-red-500/30 hover:bg-red-500/20",
};

export function Button({ variant = "primary", size = "md", className = "", children, ...props }) {
  const sizes = { sm: "px-2.5 py-1.5 text-xs", md: "px-4 py-2 text-sm" };
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed ${sizes[size]} ${BUTTON_VARIANTS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export const inputClass =
  "block w-full rounded-lg border border-line-strong bg-raised px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 " +
  "transition-colors hover:border-slate-500 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400/30 " +
  "disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-red-400/70";

export const linkButtonClass =
  "inline-flex items-center gap-1 text-sm font-medium text-indigo-300 transition-colors hover:text-indigo-200";

export function Field({ label, hint, error, children, id: givenId }) {
  const generated = useId();
  const id = givenId || generated;
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-300">{label}</label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint && !error && <p id={`${id}-hint`} className="mt-1.5 text-xs text-slate-500">{hint}</p>}
      {error && <p id={`${id}-error`} className="mt-1.5 text-xs text-red-300">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- states

export function Spinner({ className = "h-4 w-4 text-indigo-300" }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-20" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function LoadingState({ label = "Loading…" }) {
  return (
    <div role="status" className="flex items-center justify-center gap-3 py-16 text-sm text-slate-400">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Something went wrong" }) {
  const details = Array.isArray(error?.details) ? error.details : null;
  return (
    <div role="alert" className="flex gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
      <AlertIcon className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
      <div className="min-w-0">
        <p className="font-semibold text-red-100">{title}</p>
        <p className="mt-1">{error?.message || String(error)}</p>
        {details && (
          <ul className="mt-2 list-disc pl-5">
            {details.map((d, i) => (
              <li key={i}>{d.location ? <code className="font-mono">{d.location}</code> : null} {d.message}</li>
            ))}
          </ul>
        )}
        {onRetry && (
          <Button variant="danger" size="sm" className="mt-3" onClick={onRetry}>Try again</Button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, children, action, icon }) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong bg-surface/50 px-6 py-12 text-center">
      {icon && <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-raised text-slate-400">{icon}</div>}
      <p className="text-sm font-semibold text-slate-200">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-xl text-sm text-slate-400">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Notice({ tone = "info", title, children }) {
  const styles = {
    info: "border-indigo-400/25 bg-indigo-500/10 text-indigo-100",
    warning: "border-amber-400/25 bg-amber-400/10 text-amber-100",
    success: "border-emerald-400/25 bg-emerald-500/10 text-emerald-100",
  }[tone];
  const Icon = tone === "warning" ? AlertIcon : InfoIcon;
  return (
    <div className={`flex gap-3 rounded-xl border px-4 py-3 text-sm ${styles}`}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0 opacity-80" />
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? "mt-1 opacity-90" : "opacity-90"}>{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tables

export const tableClass = "min-w-full text-sm";
export const theadClass = "border-b border-line bg-raised/60 text-xs uppercase tracking-wide text-slate-400";
export const rowClass = "border-b border-line/70 transition-colors last:border-0 hover:bg-raised/50";

// A file path that wraps only after "/" instead of mid-name.
export function PathText({ path }) {
  const parts = path.split("/");
  return (
    <>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && (
            <>
              /<wbr />
            </>
          )}
        </span>
      ))}
    </>
  );
}

// Column header button for sortable tables; exposes aria-sort on the <th>.
export function SortHeader({ label, column, sort, onSort, align = "left" }) {
  const active = sort.column === column;
  const ariaSort = active ? (sort.direction === "asc" ? "ascending" : "descending") : "none";
  return (
    <th scope="col" aria-sort={ariaSort} className={`px-3 py-2.5 font-medium ${align === "right" ? "text-right" : "text-left"}`}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 rounded uppercase tracking-wide transition-colors hover:text-slate-100 ${active ? "text-slate-100" : ""}`}
      >
        {label}
        <span aria-hidden="true" className={active ? "text-indigo-300" : "text-slate-600"}>
          {active ? (sort.direction === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

// ---------------------------------------------------------------- modal

// Accessible dialog: focus moves in on open, Tab stays inside, Escape and the
// backdrop close it, and focus returns to whatever opened it.
export function Modal({ open, title, onClose, children, footer }) {
  const panel = useRef(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    const focusable = () =>
      panel.current?.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') || [];
    (focusable()[0] || panel.current)?.focus();

    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      } else if (event.key === "Tab") {
        const items = [...focusable()];
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative flex max-h-[90vh] w-full max-w-2xl flex-col rounded-t-2xl border border-line-strong bg-surface shadow-2xl sm:rounded-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <h2 id={titleId} className="min-w-0 break-words font-mono text-sm font-semibold text-white">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close dialog"
                  className="rounded-md p-1 text-slate-400 transition-colors hover:bg-raised hover:text-white">
            <CloseIcon className="h-5 w-5" />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}
