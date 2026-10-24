# RepoRisk development guide

RepoRisk has three parts:

| Part | Where | What it does |
|---|---|---|
| Engine + CLI | `reporisk/` | Git mining, filtering, bug-fix detection, SZZ, dataset, model (`python -m reporisk …`, `python -m reporisk.model …`) |
| API | `reporisk/api/` | FastAPI app: login/sessions, plus background analysis jobs |
| Dashboard | `frontend/` | React + Vite + Tailwind single-page app (dark theme) |

## Requirements

- **Python 3.9+.** The project venv uses Python 3.9.6, and every pinned version supports it.
- **Node.js 20+ and npm.** Tested with Node 24.19 / npm 11.17.
- **git** on `PATH`.

## One-time setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt       # engine, CLI and tests
pip install -r requirements-api.txt   # FastAPI, uvicorn, argon2-cffi, httpx
pip install -r requirements-ml.txt    # optional: scikit-learn for the logistic regression
cd frontend && npm install && cd ..

cp .env.example .env                  # then edit REPORISK_REPOS (and anything else)
```

### Create an account

There is **no public sign-up and no default account**. RepoRisk reads repositories on the server's filesystem, so accounts are provisioned by whoever runs it:

```bash
python -m reporisk.api.users create you@example.com    # prompts for the password twice
```

| Command | What it does |
|---|---|
| `python -m reporisk.api.users list` | Show accounts and their last login |
| `python -m reporisk.api.users set-password EMAIL` | Change a password (signs that user out everywhere) |
| `python -m reporisk.api.users deactivate EMAIL` | Block sign-in and end that user's sessions |
| `python -m reporisk.api.users activate EMAIL` | Re-enable an account |
| `python -m reporisk.api.users delete EMAIL` | Remove an account |

**Password rules:** at least 12 characters, at most 256, and not the same as the email address.

The command reads the password from a hidden prompt. For scripts, add `--password-stdin` and pipe the password in. Passwords are never accepted as command-line arguments, because those end up in shell history.

Accounts and sessions live in the SQLite file `REPORISK_DB_PATH`, which defaults to `instance/reporisk.sqlite3` and is git-ignored.

### Demo account (local development only)

In development (`npm run dev`), the sign-in page shows a **Demo Credentials** box with `demo@reporisk.local` / `RepoRiskDemo2026!`. The box only displays the credentials; the account must exist on the server. Create it once with the normal account command:

```bash
printf '%s\n' 'RepoRiskDemo2026!' | python -m reporisk.api.users create demo@reporisk.local --password-stdin
```

Run that again on a fresh database (it reports "already exists" if the account is there). To restore the demo password, use `set-password` instead of `create`, with the same input. To remove the account: `python -m reporisk.api.users delete demo@reporisk.local`.

- **Production builds hide the box.** `npm run build` doesn't include it (the credentials aren't even in the bundle) unless you deliberately build with `VITE_SHOW_DEMO_CREDENTIALS=true`, for example for a public demo.
- **Never create this account on a real deployment.** Its password is published on the page and in this file.

## Run the app locally

**Terminal 1, API.** Run it from the repository root, so it finds `.env`:

```bash
source .venv/bin/activate
python -m uvicorn reporisk.api.main:app --host 127.0.0.1 --port 8000
# equivalent: python -m uvicorn reporisk.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

The first log lines summarize the mode (development/production), whether `/docs` is on, cookie security, repositories and accounts, with warnings for anything that blocks sign-in or analysis.

**Terminal 2, dashboard:**

```bash
cd frontend
npm run dev
```

Open <http://localhost:5173> and sign in. Vite proxies `/api` and `/health` to the API, so the browser only ever talks to `localhost:5173`, and the session cookie is first-party.

Interactive API docs are at <http://127.0.0.1:8000/docs>, in development only.

> **Upgrading from 0.1:** the API entry point moved from `reporisk.api.app:app` to `reporisk.api.main:app`, and the API now needs a `.env` (or environment variables) and an account. Restart both terminals after pulling.

## Configuration

**Backend (`.env` or the environment).** Real environment variables win over `.env`; see `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `REPORISK_ENV` | `production` | `development` allows plain-HTTP cookies and `/docs`. `production` forces `Secure` cookies, disables `/docs`, and refuses to start with insecure settings |
| `REPORISK_COOKIE_SECURE` | `true` | Only `false` in `development` is honoured (for `http://localhost`) |
| `REPORISK_REPOS` | *(none)* | Optional pre-approved local repositories, separated by `:` (`;` on Windows). Clients refer to them by id, never by path |
| `REPORISK_CLONE_DIR` | system temp dir | Where public GitHub repositories are cloned; each analysis gets its own folder, deleted when it ends |
| `REPORISK_CLONE_TIMEOUT` | `180` | Seconds allowed for a clone |
| `REPORISK_MAX_REPO_MB` | `500` | Largest clone accepted |
| `REPORISK_MAX_COMMITS` | `20000` | Most commits accepted (bounds analysis time) |
| `REPORISK_MAX_QUEUED` | `3` | Analyses queued or running at once; more get `429` |
| `REPORISK_ADMIN_EMAIL`, `REPORISK_ADMIN_PASSWORD` | *(unset)* | Optional account created or updated at each start (for hosts without a shell or disk). Set both or neither; keep the password a secret in the host's dashboard |
| `REPORISK_DB_PATH` | `instance/reporisk.sqlite3` | Accounts and sessions (SQLite) |
| `REPORISK_DATA_DIR` | `output/api` | Generated datasets and model metrics per analysis |
| `REPORISK_SESSION_HOURS` | `12` | Session lifetime |
| `REPORISK_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Origins allowed to make credentialed or state-changing requests |
| `REPORISK_FRONTEND_DIST` | *(unset)* | Serve the built frontend from the API (production, same origin) |

**Frontend (`frontend/.env.local`).** See `frontend/.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | empty (same origin) | Only set this if the API is on another origin, which is not recommended (see below) |
| `REPORISK_API_PROXY` | `http://127.0.0.1:8000` | Where the Vite dev server forwards `/api` |

## Authentication design

| Concern | How it's handled |
|---|---|
| Password storage | Argon2id via `argon2-cffi`; hashes are upgraded on login if the parameters change. Plain passwords are never stored or logged |
| Credential check | Server-side. Unknown emails are checked against a dummy hash, so both failure paths take about the same time and return the same message (`Incorrect email or password.`) |
| Sessions | A random 256-bit token in the cookie `reporisk_session`, set with `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` in production, and `Max-Age` = session lifetime. The database stores only the token's SHA-256 hash |
| Logout and revocation | Logout deletes the session row, so a copied cookie stops working immediately. Changing a password or deactivating an account deletes all of that user's sessions |
| CSRF | Login and `/api/auth/me` return a per-session `csrf_token`. The browser keeps it **in memory only** and sends it as `X-CSRF-Token` on every `POST`; the server compares it in constant time. `SameSite=Strict` and an `Origin` check (foreign origins get `403`) back it up |
| Brute force | After 5 failed logins for an email within 15 minutes, further attempts get `429` with `Retry-After` |
| Authorization | Every `/api/*` route except `POST /api/auth/login` requires a session. The repository allowlist and id-only selection are unchanged; no path ever crosses the API |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and a `Content-Security-Policy` (`default-src 'self'`, `frame-ancestors 'none'`). `/api/*` responses are `Cache-Control: no-store` |
| Browser storage | Nothing sensitive is stored: no token or password in `localStorage` or `sessionStorage`, and the session cookie is unreadable by JavaScript |

## Deploying

The supported shape is **one origin over HTTPS**: FastAPI serves both the API and the built frontend, behind a TLS-terminating reverse proxy such as Caddy or nginx.

```bash
cd frontend && npm ci && npm run build && cd ..
# environment (or .env) on the server:
#   REPORISK_ENV=production
#   REPORISK_REPOS=/srv/repos/project-a:/srv/repos/project-b
#   REPORISK_DB_PATH=/var/lib/reporisk/reporisk.sqlite3
#   REPORISK_DATA_DIR=/var/lib/reporisk/output
#   REPORISK_FRONTEND_DIST=/srv/reporisk/frontend/dist
#   REPORISK_CORS_ORIGINS=https://reporisk.example.com
python -m reporisk.api.users --db /var/lib/reporisk/reporisk.sqlite3 create you@example.com
python -m uvicorn reporisk.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers --workers 1
```

- **Production settings:** cookies are always `Secure`, so the site **must** be served over HTTPS; the API refuses to start with `REPORISK_COOKIE_SECURE=false` in production. `/docs` is disabled.
- **One worker.** Analysis jobs and the login throttle are kept in process memory, so use a single worker.
- **The account database:** keep `REPORISK_DB_PATH` on persistent storage outside the web root, and back it up.
- **Split origins aren't supported.** `SameSite=Strict` cookies aren't sent cross-site, so serve the frontend and API from the same site.

## Analyzing a public GitHub repository

On **Run analysis**, enter a URL such as `https://github.com/psf/requests` and press **Start analysis**. The flow:

1. **The URL is checked twice,** by the dashboard and by the API (`reporisk/api/github.py`). Only `https://github.com/<owner>/<repo>` is accepted, with an optional trailing `/` or `.git`. Everything else is rejected with `422`:
   - other hosts, `http`, `ssh` and `file`;
   - local paths and IP addresses;
   - credentials, ports, and `/tree/...` or `?query` URLs.
2. **The API queues a job and returns at once.** The worker clones into a fresh temporary folder (`REPORISK_CLONE_DIR`). It clones from a URL **rebuilt from the validated owner/name**, so the user's text is never passed to git. The clone settings:
   - `git` is run with an argument list (no shell) and a timeout;
   - credential prompts and stored credentials are disabled, so only public repositories work and this machine's GitHub access is never used;
   - https only, with no redirects;
   - `--no-checkout`, because only history is read, so no repository files are written and nothing from the repository is executed.
3. **Size and commit-count limits are checked,** then the **unchanged** analysis pipeline runs on the clone.
4. **The clone is deleted when the job ends,** whether it succeeds or fails. Results appear in the normal dashboard pages.

Errors are shown as follows:
- **Invalid URLs** appear on the form.
- **Clone problems** ("not found or not public", timeout, too large, too many commits) appear as "Could not clone …".
- **Later failures** appear as "Analysis of … failed".

Messages never include server paths.

### Vercel (frontend) + Render (API)

This is how `https://repo-risk.vercel.app` is deployed. Vercel **proxies** `/api/*` and `/health` to Render (`frontend/vercel.json`), so the browser only ever talks to the Vercel origin. That keeps the session cookie first-party: `SameSite=Strict`, CSRF tokens and the `Origin` check all keep working. Calling Render directly from the browser would be cross-site, and the cookie would not be sent. Leave `VITE_API_BASE_URL` **unset** on Vercel.

**Render (API)**
- `render.yaml` at the repository root defines the service. In Render: **New → Blueprint →** select this repository.
- **Build command:** `pip install -r requirements.txt -r requirements-api.txt -r requirements-ml.txt`. All three files are needed: the API imports the analysis engine and the model.
- **Start command:** `uvicorn reporisk.api.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips "*"`.
- **Health check:** `/health`.
- **Secrets:** Render asks for `REPORISK_ADMIN_EMAIL` and `REPORISK_ADMIN_PASSWORD`. That account is created, or its password updated, at every start, because the free plan has no shell to run `python -m reporisk.api.users` and no persistent disk. Use a strong password (at least 12 characters), never commit it, and **never** put it in a `VITE_*` variable: those are compiled into the public JavaScript.

**Vercel (frontend)**
- Root directory `frontend`, framework Vite, output `dist`. `frontend/vercel.json` holds the rewrites to the Render URL; update it if the Render URL changes.

**Free-tier behaviour**

| Limitation | Effect |
|---|---|
| Idle sleep | The API sleeps after about 15 minutes without traffic. The first request then waits for a cold start (often 30–60 s, sometimes longer), and the sign-in page may show "Cannot reach RepoRisk" until it wakes; **Try again** recovers |
| Ephemeral disk | Restarts and redeploys wipe `/tmp/reporisk`: sessions (you sign in again), analysis results and generated datasets. The provisioned account is recreated at start, so sign-in keeps working |
| Slow CPU | Free instances get a fraction of a CPU. A full analysis of `psf/requests` (about 4,900 commits) peaked at 117 MB of memory and took 87 s on a laptop, so expect it to be several times slower on free Render. `REPORISK_MAX_COMMITS=10000` bounds it |
| Durable sessions and data | Needs a paid instance with a persistent disk. Mount it (e.g. at `/var/data`) and set `REPORISK_DB_PATH=/var/data/reporisk.sqlite3` and `REPORISK_DATA_DIR=/var/data/output`. No schema migration is needed: the same SQLite file and tables are used |

## How a request flows

```
Browser (React)                          FastAPI                                Engine
───────────────                          ───────                                ──────
GET /api/auth/me ─────────────────────►  session cookie → user + csrf_token
  401 → login page
POST /api/auth/login {email,password} ►  Argon2id verify → new session
  ◄── Set-Cookie: reporisk_session (HttpOnly) + {user, csrf_token}
GET /api/repositories ────────────────►  requires session
POST /api/analyze  X-CSRF-Token ──────►  session + CSRF + allowlist ─────────► background job: run_analysis()
GET /api/analysis/{id} (poll) ────────►  job status, stage i of 6                read_commits, features, risk,
GET …/summary | files | predictions | bug-fixes ──► results in memory          bug fixes, SZZ, dataset, model
POST /api/auth/logout  X-CSRF-Token ──►  delete session, clear cookie
```

## API endpoints

| Method and path | Auth | Purpose |
|---|---|---|
| `POST /api/auth/login` | public | `{email, password}` → session cookie + `{user, csrf_token, expires_at}` |
| `POST /api/auth/logout` | session + CSRF | End the session |
| `GET /api/auth/me` | session | Current user and CSRF token |
| `GET /health` | public | Status, API version, whether scikit-learn is installed |
| `GET /api/repositories` | session | Permitted repositories |
| `POST /api/analyze` | session + CSRF | Start an analysis of `{"github_url": "https://github.com/owner/repo"}` or a configured `{"repository_id": …}` → `202` with the job status (`429` if the queue is full) |
| `GET /api/analyses` | session | All jobs, newest first |
| `GET /api/analysis/{id}` | session | Job status and current stage |
| `GET /api/analysis/{id}/summary` | session | Counts, definitions, bug-fix/SZZ/dataset/model status |
| `GET /api/analysis/{id}/files` | session | Ranked files. Query: `search`, `risk_level`, `sort`, `order`, `limit`, `offset` |
| `GET /api/analysis/{id}/predictions` | session | ML probabilities (only if a model was trained), evaluation, limitations |
| `GET /api/analysis/{id}/bug-fixes` | session | Bug-fix commits with evidence and SZZ records |

**Errors** always have the shape `{"error": {"code", "message", "details"}}`:

| Status | Codes |
|---|---|
| 401 | `not_authenticated`, `invalid_credentials` |
| 403 | `csrf_failed`, `origin_not_allowed` |
| 404 | `analysis_not_found`, `repository_not_permitted`, `not_found` |
| 409 | `analysis_not_ready`, `analysis_failed`, `repository_unavailable` |
| 422 | `validation_error` |
| 429 | `too_many_attempts` |

Example with curl (development). The cookie jar holds the session, and `jq` pulls out the CSRF token:

```bash
curl -s -c jar.txt -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' \
     -d '{"email": "you@example.com", "password": "…"}' | tee login.json
CSRF=$(jq -r .csrf_token login.json)
curl -s -b jar.txt http://127.0.0.1:8000/api/repositories
curl -s -b jar.txt -X POST http://127.0.0.1:8000/api/analyze -H 'Content-Type: application/json' \
     -H "X-CSRF-Token: $CSRF" -d '{"repository_id": "requests", "options": {"horizon_days": 60, "snapshot_interval_days": 30}}'
rm jar.txt login.json
```

## The dashboard

- **Login:** a split-screen page with email and password, show/hide password, inline validation, a loading state, and clear messages for wrong credentials, throttling and an unreachable API.
- **Layout:** a sidebar (a drawer on mobile) holds the navigation, API/ML status, the signed-in user and sign-out. Routes are hash-based (`#/files`), so deep links and the back button work. The header shows the current analysis and its live progress on every page.
- **Pages:**
  - **Overview:** file counts by risk level, the risk chart, analysis details, bug-fix and SZZ totals, and the risk definitions.
  - **File risk explorer:** a searchable, sortable table. Selecting a file opens an accessible dialog explaining its risk.
  - **ML predictions:** probabilities only when a model was trained, the chronological evaluation, the label disclaimer and limitations.
  - **Bug-fix insights:** evidence, changed files, SZZ candidate commits with blamed lines, and per-record limitations.
  - **Run analysis:** the repository picker, options, progress and recent analyses.
- **Session handling:** if the session expires or is revoked, the next request returns you to the login page with a message.

### Heuristic risk levels

Defined in `reporisk/risk.py`. A file gets one factor per rule it meets:

| Factor | Rule |
|---|---|
| High churn | Lines added + deleted in the top 10% of analyzed files |
| Frequent changes | Commit count in the top 10% |
| Recent churn | 90-day churn > 0 and in the top 10% |
| Bug-fix history | ≥ 2 likely-bug-fix commits touched it |
| Concentrated ownership | ≥ 10 commits and author HHI ≥ 0.75 |
| Strong coupling | ≥ 5 commits and ≥ 50% of them also change one partner file |

The level is then **high** for 3 or more factors, **medium** for 1–2, and **low** for none. These are review indicators, not predictions.

## Tests

```bash
source .venv/bin/activate && python -m pytest       # all Python tests (engine, model, API, auth)
python -m unittest discover -s tests -t .           # same suite with unittest
cd frontend && npm test && npm run build            # frontend tests + production build
```

- **API and auth tests** (`tests/test_api.py`, `tests/test_auth.py`) use temporary databases and real temporary git repositories. The test account lives in `tests/auth_helpers.py` and is only used inside those temporary databases.
- **Frontend fixtures** are real API responses captured from the synthetic repository. After changing an API response, regenerate them:

```bash
python scripts/capture_frontend_fixtures.py
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/docs` or `/openapi.json` returns 404 `docs_disabled` | `REPORISK_ENV` is unset, so the API runs in production mode, where docs are off. Locally, run from the repo root with a `.env` copied from `.env.example` (`REPORISK_ENV=development`). The uvicorn startup log prints the mode |
| Startup log warns "no accounts exist" | Create one: `python -m reporisk.api.users create you@example.com` (same `REPORISK_DB_PATH` as the server) |
| API on a port other than 8000 | Start Vite with `REPORISK_API_PROXY=http://127.0.0.1:PORT npm run dev`, or set it in `frontend/.env.local`. No other change is needed: the browser stays same-origin through the proxy |
| "Cannot reach RepoRisk" | Start the API (Terminal 1) with `reporisk.api.main:app`; keep `npm run dev` running; check `REPORISK_API_PROXY` |
| Login says "Requests from this origin are not allowed" | The page's origin isn't the API's own origin or in `REPORISK_CORS_ORIGINS`. Use the Vite dev server (it keeps the Host header) or add the origin |
| Login succeeds but the next request says "Sign in to continue" | The cookie wasn't stored: over plain HTTP you need `REPORISK_ENV=development` and `REPORISK_COOKIE_SECURE=false`; in production, serve over HTTPS |
| `ConfigError: production requires Secure session cookies` | You set `REPORISK_COOKIE_SECURE=false` with `REPORISK_ENV=production`. Use HTTPS, or `development` locally |
| "Too many failed sign-in attempts" | Wait for the lockout (15 minutes), or restart the API; the throttle is in memory |
| Forgot a password | `python -m reporisk.api.users set-password you@example.com` |
| "No repositories configured" | Set `REPORISK_REPOS` in `.env`, then restart the API |
| ML predictions say "scikit-learn is not installed" | `pip install -r requirements-ml.txt`, then restart the API |
| `pip install` fails with `nodename nor servname provided` | DNS/network problem reaching PyPI. Check the connection and retry |
| Recent analyses disappeared | Results live in memory; restarting the API clears them |

## What is not committed

These are all git-ignored:
- `.venv/`, `frontend/node_modules/`, `frontend/dist/`;
- `.env` and `.env.*` (the `.env.example` files are kept);
- `instance/` and `*.sqlite3` (accounts and sessions);
- `output/` (datasets, metrics, predictions, API job data);
- `*.csv`, `*.jsonl`, `*.meta.json`, `metrics.json`, `*.pkl`, `*.joblib`.

The frontend test fixtures under `frontend/src/test/fixtures/` **are** committed. They are synthetic, contain no local paths, and their CSRF token is the placeholder `test-csrf-token`.

## Known limitations

- **No self-service account features.** There's no sign-up, password reset email, MFA or roles; everyone signed in sees the same repositories and analyses.
- **Process-local state.** The login throttle is per email (not per IP) and in memory. A determined attacker can temporarily lock out a known email, and the counters reset when the API restarts.
- **Results don't survive a restart.** Analysis results are in memory and shared by all users, and only one analysis runs at a time.
- **SQLite suits a small team on one server,** not a multi-instance deployment.
- **Renamed files lose history.** It is split at renames, so files moved into `src/` look newer than they are, which understates their risk.
- **ML performance is not established** on the evaluated repository, and labels are heuristic bug-fix classifications, not verified defects.
