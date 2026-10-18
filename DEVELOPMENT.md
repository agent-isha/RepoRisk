# RepoRisk development guide

RepoRisk has three parts:

| Part | Where | What it does |
|---|---|---|
| Engine + CLI | `reporisk/` | Git mining, filtering, bug-fix detection, SZZ, dataset, model (`python -m reporisk …`, `python -m reporisk.model …`) |
| API | `reporisk/api/` | FastAPI app that runs the engine as background jobs |
| Dashboard | `frontend/` | React + Vite + Tailwind single-page app that calls the API |

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
pip install -r requirements-api.txt   # FastAPI, uvicorn, httpx (API and its tests)
pip install -r requirements-ml.txt    # optional: scikit-learn for the logistic regression
cd frontend && npm install && cd ..
```

Without `requirements-ml.txt`, everything still works. The ML predictions view then says the model is unavailable, and the churn baseline is still evaluated.

## Run the app locally

**Terminal 1, API.** `REPORISK_REPOS` lists the only repositories the app may analyze, separated by `:` (use `;` on Windows):

```bash
source .venv/bin/activate
REPORISK_REPOS="$HOME/test-repos/requests" python -m uvicorn reporisk.api.app:app --host 127.0.0.1 --port 8000
```

**Terminal 2, dashboard:**

```bash
cd frontend
npm run dev
```

Open <http://localhost:5173>. Pick a repository, optionally adjust the analysis options, and press **Start analysis**. Progress updates every 1.5 s; the results tabs appear when the job completes.

Interactive API docs (OpenAPI) are at <http://127.0.0.1:8000/docs>.

### Configuration

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `REPORISK_REPOS` | API | *(none)* | Permitted local git repositories. Clients refer to them by id (the folder name), never by path |
| `REPORISK_DATA_DIR` | API | `output/api` | Where each analysis writes its dataset CSV and model metrics (git-ignored) |
| `REPORISK_CORS_ORIGINS` | API | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API |
| `VITE_API_BASE_URL` | dashboard | `http://127.0.0.1:8000` | API base URL. Copy `frontend/.env.example` to `frontend/.env.local` to change it |

## How a request flows

```
Browser (React)                    FastAPI                           Engine (existing modules)
───────────────                    ───────                           ─────────────────────────
GET /api/repositories  ─────────►  permitted repos from env
POST /api/analyze {repository_id}► validate id → JobManager.submit ─► worker thread: run_analysis()
   ◄── 202 {id, status: queued}                                        read_commits (once)
GET /api/analysis/{id} (poll) ──►  job status, stage i of 6            compute_stats, rank_files, alive_files
                                                                       detect_bug_fixes → risk.assess
                                                                       trace_fix (SZZ) per likely fix
                                                                       build_dataset → write_dataset
                                                                       run_pipeline (evaluation)
                                                                       fit_and_score (latest snapshot)
GET …/summary | files | predictions | bug-fixes ──► results kept in memory for the job
```

- **Analyses are background jobs** on a single worker thread, so requests return immediately. Results live in memory and are lost when the API restarts.
- **No path ever crosses the API.** `POST /api/analyze` accepts only a configured repository id.
- **Generated files go to `REPORISK_DATA_DIR/<analysis id>/`.** Writing inside the analyzed repository is refused.

## API endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | Status, API version, whether scikit-learn is installed |
| `GET /api/repositories` | Permitted repositories and whether each is usable |
| `POST /api/analyze` | Start an analysis → `202` with the job status |
| `GET /api/analyses` | All jobs, newest first |
| `GET /api/analysis/{id}` | Job status: `queued`, `running`, `completed` or `failed`, with the current stage |
| `GET /api/analysis/{id}/summary` | Counts, risk definitions, bug-fix/SZZ/dataset/model status |
| `GET /api/analysis/{id}/files` | Ranked files with risk factors. Query: `search`, `risk_level`, `sort`, `order`, `limit`, `offset` |
| `GET /api/analysis/{id}/predictions` | ML probabilities (only if a model was trained), evaluation, limitations |
| `GET /api/analysis/{id}/bug-fixes` | Likely/uncertain bug-fix commits with evidence and SZZ records. Query: `outcome`, `search`, `limit`, `offset` |

**Errors** always have the shape `{"error": {"code", "message", "details"}}`:

| Status | Codes |
|---|---|
| 404 | `analysis_not_found`, `repository_not_permitted`, `not_found` |
| 409 | `analysis_not_ready`, `analysis_failed`, `repository_unavailable` |
| 422 | `validation_error`, with per-field details |

Examples:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/repositories
curl -s -X POST http://127.0.0.1:8000/api/analyze -H 'Content-Type: application/json' \
     -d '{"repository_id": "requests", "options": {"horizon_days": 60, "snapshot_interval_days": 30}}'
curl -s http://127.0.0.1:8000/api/analysis/<id>
curl -s 'http://127.0.0.1:8000/api/analysis/<id>/files?sort=churn&limit=10'
curl -s 'http://127.0.0.1:8000/api/analysis/<id>/bug-fixes?outcome=likely_bug_fix&limit=5'
```

Analysis options and their defaults:

| Option | Default | Meaning |
|---|---|---|
| `exclude_tests` | `true` | Leave test files out |
| `source_only` | `true` | Only source-code files |
| `run_szz` | `true` | Trace candidate bug-inducing commits |
| `train_model` | `true` | Build the dataset and train/evaluate the model |
| `horizon_days` | `90` | Future window for the label (7–730) |
| `snapshot_interval_days` | `90` | Days between dataset snapshots (7–365) |

## What the dashboard shows

**Overview:**
- files analyzed;
- high, medium and low counts (definitions below);
- bug-fix and SZZ totals;
- ML model status, warnings, and recent analyses.

**File risk explorer:** a searchable, sortable table of churn, commits, 90-day churn, authors, coupling and bug-fix commits. Each file has factor chips, and clicking a path explains its risk. This view shows heuristics only; ML probabilities are on their own tab.

**ML predictions:**
- probabilities, but only when a model was actually trained;
- the chronological evaluation, comparing the churn baseline with logistic regression, with "undefined" shown for metrics that can't be computed;
- warnings, limitations, and the label disclaimer.

When no model is available, the tab shows an empty state with the reason.

**Bug-fix insights:** likely and uncertain bug-fix commits with their classification evidence, changed files, SZZ candidate commits with the blamed lines, and per-record limitations.

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

The level is then **high** for 3 or more factors, **medium** for 1–2, and **low** for none.

Percentile rules are relative to the files in the same analysis. These are review indicators, not predictions.

## Tests

```bash
# all Python tests (engine, model, API, frontend-fixture contract)
source .venv/bin/activate && python -m pytest

# the same suite with unittest (the API tests skip if requirements-api.txt isn't installed)
python -m unittest discover -s tests -t .

# frontend tests (Vitest + Testing Library, jsdom)
cd frontend && npm test
```

- **API tests** (`tests/test_api.py`) run real analyses on a synthetic git repository built by `tests/synthetic_repo.py`.
- **Frontend fixtures** are real API responses captured from that same repository. After changing an API response, regenerate them; the Python contract test fails if a fixture no longer matches the schemas:

```bash
python scripts/capture_frontend_fixtures.py
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "Cannot reach the RepoRisk API" | Start the API (Terminal 1). Check `VITE_API_BASE_URL` matches its host/port |
| Browser console shows a CORS error | Open the dashboard at an origin listed in `REPORISK_CORS_ORIGINS` (default: `localhost:5173` or `127.0.0.1:5173`) |
| "No repositories configured" | Set `REPORISK_REPOS` before starting uvicorn; restart the API after changing it |
| A repository shows "not a git repository" / "directory does not exist" | Use the absolute path of a folder that contains `.git` |
| ML predictions say "scikit-learn is not installed" | `pip install -r requirements-ml.txt` in the API's venv, then restart the API |
| ML predictions say "model could not be trained" | Not enough history for two complete snapshots, or only one label class. Lower the snapshot interval/horizon, or analyze a longer-lived repository |
| `pip install` fails with `nodename nor servname provided` | DNS/network problem reaching PyPI. Check the connection or proxy and retry; nothing in the repo needs to change |
| Recent analyses disappeared | Results are kept in memory; restarting the API clears them |
| Analysis of a large repo is slow | Reading history with PyDriller dominates (about 75 s for `psf/requests`). Disable SZZ or model training in the options to save the remaining time |
| `npm install` warns that `esbuild` install scripts were not approved | Safe to ignore: esbuild's platform binary ships as a separate package. If Vite fails to start, run `npm approve-scripts esbuild` |

## What is not committed

`.venv/`, `frontend/node_modules/`, `frontend/dist/`, `output/` (all datasets, metrics, predictions and API job data), `*.csv`, `*.jsonl`, `*.meta.json`, `metrics.json`, model files and `.env*` files are git-ignored. The frontend test fixtures under `frontend/src/test/fixtures/` **are** committed. They are small, synthetic and contain no local paths.
