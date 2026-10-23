import { useState } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { AlertIcon, CheckIcon, EyeIcon, EyeOffIcon, InfoIcon, LogoMark } from "../components/icons.jsx";
import { Button, Field, Spinner, inputClass } from "../components/ui.jsx";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Local demo account, shown only on the Vite dev server or when a build sets
// VITE_SHOW_DEMO_CREDENTIALS=true. The account must be created on the server
// (see DEVELOPMENT.md); showing it here grants nothing by itself.
export const DEMO_CREDENTIALS = { email: "demo@reporisk.local", password: "RepoRiskDemo2026!" };
export const showDemoCredentials = () =>
  import.meta.env.DEV || import.meta.env.VITE_SHOW_DEMO_CREDENTIALS === "true";

function validate({ email, password }) {
  const errors = {};
  if (!email.trim()) errors.email = "Enter your email address.";
  else if (!EMAIL_PATTERN.test(email.trim())) errors.email = "Enter a valid email address, like you@example.com.";
  if (!password) errors.password = "Enter your password.";
  return errors;
}

function messageFor(error) {
  if (error.code === "invalid_credentials") return "Incorrect email or password.";
  if (error.code === "too_many_attempts") return error.message;
  if (error.code === "network_error") return "Can't reach the RepoRisk server. Check that the API is running and try again.";
  return error.message || "Sign-in failed. Try again.";
}

export default function LoginPage() {
  const { login, notice } = useAuth();
  const [values, setValues] = useState({ email: "", password: "" });
  const [errors, setErrors] = useState({});
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState(null);

  const update = (key) => (event) => {
    setValues((v) => ({ ...v, [key]: event.target.value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  };

  async function submit(event) {
    event.preventDefault();
    const found = validate(values);
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length) {
      document.getElementById(found.email ? "login-email" : "login-password")?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await login(values.email.trim(), values.password);
    } catch (error) {
      setFailure(messageFor(error));
      setValues((v) => ({ ...v, password: "" }));
      setSubmitting(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <BrandPanel />

      <main className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <LogoMark />
            <span className="text-lg font-semibold text-white">RepoRisk</span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-white">Sign in</h1>
          <p className="mt-2 text-sm text-slate-400">Use the account your RepoRisk administrator created for you.</p>

          {notice && (
            <p role="status" className="mt-6 flex gap-2 rounded-lg border border-indigo-400/25 bg-indigo-500/10 px-3 py-2.5 text-sm text-indigo-100">
              <InfoIcon className="mt-0.5 h-4 w-4 shrink-0" />
              {notice}
            </p>
          )}
          {failure && (
            <p role="alert" className="mt-6 flex gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5 text-sm text-red-200">
              <AlertIcon className="mt-0.5 h-4 w-4 shrink-0" />
              {failure}
            </p>
          )}

          <form onSubmit={submit} noValidate className="mt-6 space-y-5">
            <Field label="Email" id="login-email" error={errors.email}>
              {(props) => (
                <input {...props} type="email" name="email" autoComplete="username" inputMode="email"
                       className={inputClass} value={values.email} onChange={update("email")}
                       disabled={submitting} placeholder="you@example.com" />
              )}
            </Field>

            <Field label="Password" id="login-password" error={errors.password}>
              {(props) => (
                <div className="relative">
                  <input {...props} type={showPassword ? "text" : "password"} name="password"
                         autoComplete="current-password" className={`${inputClass} pr-11`}
                         value={values.password} onChange={update("password")} disabled={submitting} />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    aria-pressed={showPassword}
                    aria-controls="login-password"
                    className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-lg text-slate-400 transition-colors hover:text-slate-100"
                  >
                    {showPassword ? <EyeOffIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
                  </button>
                </div>
              )}
            </Field>

            <Button type="submit" className="w-full py-2.5" disabled={submitting}>
              {submitting && <Spinner className="h-4 w-4 text-white" />}
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          {showDemoCredentials() && <DemoCredentials />}

          <p className="mt-8 border-t border-line pt-6 text-xs leading-relaxed text-slate-500">
            There is no public sign-up: RepoRisk reads repositories on the server, so accounts are created by whoever
            runs it with <code className="font-mono text-slate-400">python -m reporisk.api.users create</code>.
          </p>
        </div>
      </main>
    </div>
  );
}

const FEATURES = [
  "Mines git history for churn, ownership and change coupling",
  "Flags risky files with transparent, explainable rules",
  "Traces heuristic bug fixes back to candidate commits (SZZ)",
  "Reports ML evaluation honestly, including when it is weak",
];

function BrandPanel() {
  return (
    <aside className="relative hidden overflow-hidden border-r border-line bg-gradient-to-br from-[#0f1730] via-[#0b1124] to-canvas lg:flex lg:flex-col lg:justify-between lg:p-12">
      <div aria-hidden="true" className="pointer-events-none absolute -left-32 -top-32 h-96 w-96 rounded-full bg-indigo-600/20 blur-3xl" />
      <div className="relative flex items-center gap-3">
        <LogoMark className="h-9 w-9" />
        <span className="text-lg font-semibold tracking-tight text-white">RepoRisk</span>
      </div>

      <div className="relative max-w-lg">
        <h2 className="text-3xl font-semibold leading-tight tracking-tight text-white">
          See where your codebase is fragile, from the history it already has.
        </h2>
        <ul className="mt-6 space-y-3 text-sm text-slate-300">
          {FEATURES.map((f) => (
            <li key={f} className="flex gap-3">
              <CheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-indigo-300" />
              {f}
            </li>
          ))}
        </ul>
        <AnalyticsIllustration />
      </div>

      <p className="relative text-xs text-slate-500">
        Risk levels are heuristics and labels are not verified defects. RepoRisk shows its evidence and limitations.
      </p>
    </aside>
  );
}

// Decorative illustration of the dashboard's visual language; not real data.
function AnalyticsIllustration() {
  const bars = [
    { w: 92, tone: "#f87171" }, { w: 74, tone: "#fbbf24" }, { w: 61, tone: "#fbbf24" },
    { w: 43, tone: "#34d399" }, { w: 30, tone: "#34d399" },
  ];
  return (
    <figure aria-hidden="true" className="mt-10 rounded-2xl border border-line-strong/70 bg-surface/70 p-5 shadow-2xl backdrop-blur">
      <div className="mb-4 flex items-center justify-between">
        <div className="h-2.5 w-28 rounded-full bg-slate-600/60" />
        <div className="flex gap-1.5">
          <span className="h-2 w-2 rounded-full bg-red-400/80" />
          <span className="h-2 w-2 rounded-full bg-amber-300/80" />
          <span className="h-2 w-2 rounded-full bg-emerald-400/80" />
        </div>
      </div>
      <div className="grid grid-cols-[1fr_auto] gap-6">
        <div className="space-y-3">
          {bars.map((b, i) => (
            <div key={i} className="flex items-center gap-3">
              <div className="h-2 w-16 rounded-full bg-slate-700" />
              <div className="h-2 flex-1 rounded-full bg-slate-800">
                <div className="h-2 rounded-full" style={{ width: `${b.w}%`, background: b.tone, opacity: 0.85 }} />
              </div>
            </div>
          ))}
        </div>
        <svg viewBox="0 0 120 80" className="h-24 w-36">
          <defs>
            <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#818cf8" stopOpacity="0.45" />
              <stop offset="100%" stopColor="#818cf8" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d="M0 62 L18 54 L34 58 L50 38 L66 44 L82 24 L98 30 L120 12 L120 80 L0 80 Z" fill="url(#spark)" />
          <path d="M0 62 L18 54 L34 58 L50 38 L66 44 L82 24 L98 30 L120 12" fill="none" stroke="#818cf8" strokeWidth="2" />
        </svg>
      </div>
      <figcaption className="mt-4 text-[11px] uppercase tracking-wider text-slate-500">Illustration</figcaption>
    </figure>
  );
}

function DemoCredentials() {
  return (
    <section aria-labelledby="demo-credentials-title"
             className="mt-6 rounded-lg border border-indigo-400/25 bg-indigo-500/10 px-4 py-3 text-sm">
      <h2 id="demo-credentials-title" className="font-semibold text-indigo-100">Demo Credentials</h2>
      <dl className="mt-2 space-y-1.5">
        {[["Email", DEMO_CREDENTIALS.email], ["Password", DEMO_CREDENTIALS.password]].map(([label, value]) => (
          <div key={label} className="flex items-center gap-2">
            <dt className="w-20 shrink-0 text-slate-400">{label}</dt>
            <dd className="min-w-0 flex-1 break-all font-mono text-slate-100">{value}</dd>
            <CopyButton label={label} value={value} />
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-slate-400">For local demos only. Create this account first (see DEVELOPMENT.md).</p>
    </section>
  );
}

function CopyButton({ label, value }) {
  const [state, setState] = useState("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 1500);
  }
  return (
    <button type="button" onClick={copy} aria-label={`Copy demo ${label.toLowerCase()}`}
            className="shrink-0 rounded-md px-2 py-0.5 text-xs font-medium text-indigo-200 ring-1 ring-inset ring-indigo-400/30 transition-colors hover:bg-indigo-500/20">
      <span aria-live="polite">{state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}</span>
    </button>
  );
}
