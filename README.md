# RepoRisk

Mines a local git repository's history and ranks files by risk signals: churn, recent activity, ownership concentration and change coupling.

It can be used from the command line (below) or through a web dashboard backed by a FastAPI service.
<h1 align="center">
  🚀 <a href="https://repo-risk.vercel.app">TRY THE APPLICATION HERE</a> 🚀
</h1>
<p align="center">Analyze GitHub repositories and identify potentially risky commits.</p>

**Web app quick start** (full details, including deployment, in **[DEVELOPMENT.md](DEVELOPMENT.md)**):

```bash
pip install -r requirements.txt -r requirements-api.txt   # + requirements-ml.txt for the ML model
cp .env.example .env                                       # REPORISK_ENV=development for local use
python -m reporisk.api.users create you@example.com        # accounts are provisioned, no public sign-up
python -m uvicorn reporisk.api.main:app --port 8000        # terminal 1
cd frontend && npm install && npm run dev                  # terminal 2 → http://localhost:5173
```

The dashboard requires sign-in. Passwords are hashed with Argon2id, sessions use HttpOnly `SameSite=Strict` cookies with server-side revocation and CSRF tokens, and users analyze public GitHub repositories by URL (e.g. `https://github.com/psf/requests`); the server clones them into temporary folders that are deleted after each analysis. Pre-approved local repositories can still be listed in `REPORISK_REPOS`. Risk levels are heuristics, bug-fix labels are not verified defects, and the ML model's predictive performance is not established. The dashboard shows these limitations next to the results.

## Usage

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m reporisk /path/to/repo --top 15
```

| Option | Effect |
|---|---|
| `--top N` | Show the top N files (default 10) |
| `--cutoff YYYY-MM-DD` | Analyze history up to the end of that day (UTC). Default: the latest commit |
| `--exclude-tests` | Leave test files out of the ranking |
| `--include-docs` | Keep README/changelog files and `docs/` directories in the ranking |
| `--exclude GLOB` | Extra pattern to leave out, e.g. `"*.rst"` (repeatable) |
| `--bug-fixes` | Instead of the ranking, summarize heuristic bug-fix commit candidates. `--top N` sets examples per outcome; `--cutoff` also applies |
| `--szz` | Instead of the ranking, trace `likely_bug_fix` commits back to candidate bug-inducing commits. `--top N` sets examples; `--cutoff` limits which fixes are traced |
| `--szz-output FILE.jsonl` | With `--szz`, also write one JSON record per trace. `*.jsonl` is git-ignored |
| `--build-dataset` | Instead of the ranking, build the leakage-safe prediction dataset (see [Prediction dataset](#prediction-dataset)) |

Run the tests:

```bash
python -m unittest discover -s tests -t . -v
```

## How it works

1. `read_commits()` reads every non-merge commit with PyDriller. This is the only code that touches git, and it only reads.
2. `compute_stats()` turns those commits into raw per-file metrics for **every** file, including noise.
3. `rank_files()` applies the noise/doc/test filters (`reporisk/filters.py`) and sorts by churn. Filtering never changes the raw metrics.

## Analysis cutoff

All metrics are computed **as of a cutoff instant**. Commits dated after it are ignored entirely: they don't count toward churn, authors, coupling or the commit total. This matters later for machine learning: features must only use history up to the cutoff, so the model can't see the future.

- The default is the latest commit's committer date, so every commit is used.
- `--cutoff 2024-06-30` means `2024-06-30T23:59:59.999999` UTC.
- Dates are compared as instants, so timezones are handled correctly.

## Metrics

| Column | Field | Meaning |
|---|---|---|
| `churn` | `churn` | Lines added + deleted over all analyzed history |
| `commits` | `commits` | Number of commits that touched the file |
| `authors` | `authors` | Distinct author emails |
| `c30d`, `c90d`, `c180d` | `recent_churn[days]` | Lines added + deleted in commits with `cutoff − N days < date ≤ cutoff`. The window start is excluded, the cutoff included |
| `hhi` | `author_hhi` | Herfindahl–Hirschman Index of each author's share of the file's commits, `Σ shareᵢ²` |
| `coupled` | `coupled_files` | Number of other files changed in the same commit at least `MIN_COCHANGES_FOR_COUPLING` (2) times |
| `cpl` | `strongest_coupling` | Shared commits with the top partner ÷ this file's commits. 0.40 means 40% of this file's commits also touched its top partner |
| — | `top_partner` | The file it most often changes with (ties broken alphabetically) |

**Reading HHI.**
- `1.0` means a single author made every commit, a bus-factor risk.
- `1/n` means n authors contributed evenly. For example, `0.25` is four equal authors.
- Low values on a busy file mean ownership is diffuse.
- A file with no commits has no HHI (`None`, printed as `-`) instead of a misleading 0.

**How coupling is stored.** Only pairs that actually changed together are kept: a sparse counter keyed `(path_a, path_b)` with `path_a < path_b`. Every possible pair is never stored. On `psf/requests` that is 6,652 stored pairs out of 108,345 possible.

Commits touching more than `MAX_FILES_PER_COMMIT_FOR_COUPLING` (50) files are skipped for coupling only. Bulk reformats, renames and vendoring would otherwise add thousands of meaningless pairs; their churn and author counts are still recorded.

## Limitations

- **Renames split history.** A renamed file is counted as two separate files: changes before the rename are recorded under the old path.
- **Author identity is the email address.** One person with two emails counts as two authors, and `.mailmap` is not applied, so HHI and author counts can be off for long-lived repos.
- **Line churn treats every line equally.** Reformatting, generated code and binary files that git diffs as text all inflate churn. The noise filter removes the common cases only.
- **Recent churn depends on the cutoff.** For a quiet repo, the windows before the latest commit can be all zero, as they mostly are on `requests` today. Choose a cutoff inside an active period when you need recent signal.
- **Dates are committer dates.** Rebases and cherry-picks move them, so recent-churn windows can include work that was authored earlier.
- **Coupling ignores noise filters.** Raw coupling includes every file, so a file's top partner can be a changelog or lock file. Filters are applied only when ranking.
- **Coupling depends on commit habits.** Small, focused commits understate coupling; large, mixed commits overstate it. Merge commits are excluded.
- **`strongest_coupling` is asymmetric.** It is divided by this file's own commits, so a rarely changed file can show high coupling to a busy partner while the partner shows low coupling back.
- **`first_seen` / `last_changed` follow traversal order** (oldest first), not min/max date, so rewritten history can make them slightly off.

## Bug-fix commit detection

`reporisk/bug_fixes.py` labels every non-merge commit with one of three outcomes:

| Outcome | Meaning |
|---|---|
| `likely_bug_fix` | Strong defect wording, or at least two different fix-related terms, and no signs of a non-bug change |
| `uncertain` | Some fix wording but not enough to be sure, mixed signals, or only test files changed |
| `not_likely_bug_fix` | No fix wording, a typo/format/docs/dependency/CI change, or a revert |

**These are heuristic labels, not ground truth.** Nobody verified them by hand, and they will be wrong for some commits. Treat `likely_bug_fix` as a noisy positive label, and decide explicitly whether downstream work keeps or drops `uncertain` commits.

```bash
python -m reporisk /path/to/repo --bug-fixes --top 10
```

### How a commit is classified

Terms are case-insensitive regexes with word boundaries, so `fix` does not match `prefix` or `fixture`, and `bug` does not match `debug`. They are matched in the **subject line** only.

| Group | Examples | Effect |
|---|---|---|
| Strong | bug, bugfix, hotfix, regression, defect, crash, segfault, `fix #123` | One is enough for `likely` |
| Weak | fix, patch, resolve, incorrect, broken, wrong, error, exception, fail | Two different ones give `likely`; one gives `uncertain` |
| Non-bug | typo, spelling, whitespace, formatting, lint, pep8, black, docs, readme, changelog, comment, links, error message, ci, chore, bump, upgrade, dependency, vendor, release, revert, merge conflict | With weak terms only → `not_likely`; with a strong term → `uncertain` (mixed) |

The commit body is checked only for issue references:
- `Fixes #N` counts as strong.
- `Closes #N` and `Resolves #N` count as weak, because closed issues are often feature requests.

Ignoring the rest of the body is deliberate: bodies often quote release notes ("bug fixes") or unrelated discussion.

The changed files are checked too:

| Changed files | Effect |
|---|---|
| All documentation | `not_likely` |
| All dependency files (`requirements*.txt`, lock files) | `not_likely` |
| All CI config (`.github/`, `.travis.yml`, etc.) | `not_likely` |
| All tests | Capped at `uncertain`, since fixing a test usually means a flaky test, not a product bug |

Each result records its **evidence**, e.g. `strong:regression`, `weak:fix`, `non-bug:typo`, `files:docs-only`. This shows exactly why a commit got its label. All term lists, file patterns and the weak-term threshold live in `BugFixRules`, so they can be changed per repository.

### Bug-fix limitations

- **Vague messages are missed.** Real fixes described as "Handle empty header" or "Update models.py" get no label. On `psf/requests` many genuine one-word "Fix …" commits land in `uncertain`.
- **Precision is unmeasured.** No commits have been hand-labelled yet, so the share of `likely` commits that are real bugs is unknown. Label a random sample of about 50 per outcome before trusting the labels.
- **English only.** Terms are English, and conventions differ by project; some write `[BUG]`, others only reference issue numbers.
- **Issue references are not looked up.** `Fixes #123` is trusted without checking that issue #123 is a bug report.
- **Merge commits are excluded.** Projects whose only fix wording sits in the merge commit ("Merge pull request #12 from user/fix-crash") will be under-counted.
- **One label per commit.** A commit mixing a bug fix with a refactor is labelled as a whole, and every file it touched is attributed to the fix.
- **Labels mark the fixing commit, not the bug.** Finding the commit that *introduced* each bug (SZZ) is a later step.

## SZZ-style tracing

`reporisk/szz.py` takes each `likely_bug_fix` commit and works backwards:

1. Read the fix's diff, skipping merge and root commits.
2. Collect the lines it **deleted or changed** in source files. These are assumed to be the faulty lines.
3. Run `git blame -w` on the fix's **parent** for exactly those lines. The commit that last touched each line becomes a **candidate bug-inducing commit**.
4. Group the lines by `(fix, candidate, file)` into a `TraceRecord`.

```bash
python -m reporisk /path/to/repo --szz --top 10 --szz-output output/szz.jsonl
```

Each `TraceRecord` contains:
- `fix_hash`
- `inducing_hash` and `inducing_date`
- `path`: the file's path in the fix's parent
- `lines`: each line's number before the fix, its number in the inducing commit, and its content
- `evidence`: why this candidate was chosen
- `limitations`: caveats for this particular record

Each fix's `TraceResult` also lists the files that were skipped and why, or an `error` when the whole commit could not be traced.

**Candidates, not causes.** Blame names the last commit to touch a line. That commit may have introduced the bug, or it may only have moved, renamed or reformatted the line. Nothing here proves which commit caused a bug.

### Configuration (`SZZConfig`)

| Setting | Default | Why |
|---|---|---|
| `trace_test_files` | `False` | Deleted test lines rarely point at the product commit that caused a bug |
| `trace_doc_files` | `False` | Documentation changes are not code defects |
| `trace_noise_files` | `False` | Lock files, generated and vendored code |
| `ignore_whitespace` | `True` | `git blame -w`, so a reindent does not hide the original author |
| `skip_blank_lines`, `skip_comment_lines` | `True` | No behaviour to blame |
| `skip_trivial_lines` | `True` | Lines with no letters or digits (`)`, `});`) and lone keywords (`try:`, `else:`) mostly point at whoever last reformatted the block |
| `max_deleted_lines_per_file` | `100` | Bigger deletions look like rewrites or moves, not targeted corrections |

### How each case is handled

| Case | Handling |
|---|---|
| Merge commit | Not traced (`error`) |
| Root commit (no parent) | Not traced (`error`) |
| Unknown hash | Not traced (`error`) |
| File added by the fix | Nothing to blame; ignored |
| File deleted by the fix | Skipped: deleting a file is not a line-level correction |
| File renamed by the fix | Old path blamed on the parent; noted in `limitations` |
| Binary file | Skipped |
| Blame fails (e.g. path missing) | That file is skipped with git's message; the other files are still traced |
| Fix only adds lines | Nothing to trace; counted as "no traceable deleted lines" |
| Shallow-clone boundary commit | Traced, flagged in `limitations` |

### SZZ limitations

- **Fixes that only add lines cannot be traced.** Examples are a missing `None` check or a new `except` branch. On `psf/requests`, 29 of 157 likely fixes had nothing traceable.
- **Blame finds the last change, not the cause.** Refactors, renames and moves earlier in history get blamed instead of the real culprit. Only whitespace changes are skipped (`-w`); moved or copied code is not followed, since `-M`/`-C` are not used.
- **One fix often yields several candidates** (up to 11 on `requests`). Downstream work must decide how to weight them; not every changed line in a fix was faulty.
- **Only the first parent is used,** and changes inside merge commits are never traced.
- **Labels inherit the bug-fix detector's errors.** A false-positive "fix" produces false-positive inducing commits.
- **Comment detection is prefix-based.** `#` also matches C preprocessor lines such as `#include`, so drop it from `comment_prefixes` for C/C++ repositories. Block-comment bodies that don't start with `*` are not detected.
- **Rename chains are followed only by blame itself.** Blame follows renames within a file's history, but not splits or merges of files.
- **No time check against the bug report.** Classic SZZ refinements discard candidates dated after the bug was reported; there is no issue-tracker data here to do that.

## Prediction dataset

`reporisk/dataset.py` builds a file-level CSV for predicting **which files will receive a bug-fix commit in the next N days**. No model is trained here.

```bash
# defaults: 90-day snapshots, 90-day horizon, source files only, output/dataset.csv
python -m reporisk /path/to/repo --build-dataset --exclude-tests

# monthly snapshots, 60-day horizon, custom output path
python -m reporisk /path/to/repo --build-dataset --exclude-tests \
    --snapshot-interval 30 --horizon 60 --dataset-out output/requests_30_60.csv
```

| Option | Default | Effect |
|---|---|---|
| `--dataset-out FILE.csv` | `output/dataset.csv` | CSV path. Metadata goes beside it as `FILE.meta.json`. Parent directories are created. Writing inside the analyzed repository is refused |
| `--snapshot-interval DAYS` | 90 | Days between snapshot dates |
| `--horizon DAYS` | 90 | Length of the future label window |
| `--first-snapshot DATE` | first commit + one interval | First snapshot date (`YYYY-MM-DD`) |
| `--cutoff DATE` | latest commit | End of observed history. Later commits are ignored entirely |
| `--label-uncertain` | off | Also count `uncertain` bug-fix commits as label events |
| `--include-incomplete` | off | Keep snapshots whose future window isn't fully observed, with **blank** labels |
| `--all-file-types` | off | Rows for every file type, not only source code |
| `--exclude-tests`, `--include-docs`, `--exclude GLOB` | as for the ranking | Which files become rows |

Generated datasets are git-ignored (`output/`, `*.csv`, `*.meta.json`).

### Rows and snapshots

Each row is one **(snapshot date T, file)** pair. The snapshot instant is the end of day T in UTC, the same meaning as `--cutoff`. A file is a row at T when all of these hold:
- it exists at T, after replaying every add, rename and delete up to T;
- it has a source-code extension, unless `--all-file-types` is given;
- it passes the noise, doc and test filters.

Snapshots are taken every `--snapshot-interval` days instead of at every commit. Per-commit samples would produce thousands of near-duplicate rows of the same file.

The default interval equals the horizon, so consecutive label windows don't overlap and one future fix is never counted in two snapshots. Rows of the same file across snapshots are still correlated. **Split train and test by snapshot date** (train on earlier snapshots, test on later ones), never by random row.

### Columns

**ID columns** (not features): `snapshot_date`, `path`.

**Features.** All are computed only from commits dated at or before T:

| Column | Meaning |
|---|---|
| `commits` | Commits that touched the file |
| `lines_added`, `lines_deleted`, `churn` | Line totals; churn = added + deleted |
| `churn_30d`, `churn_90d`, `churn_180d` | Churn in the last N days before T |
| `authors` | Distinct author emails |
| `author_hhi` | Ownership concentration (1.0 = one author) |
| `coupled_files`, `strongest_coupling` | Files it co-changes with at least twice, and its strongest co-change ratio |
| `age_days` | Days since the file first appeared (under its current path) |
| `days_since_last_change` | Days since its last commit |
| `past_bug_fixes` | Qualifying bug-fix commits that touched it up to T |

**Label columns.** These are derived from the future and must never be model inputs:

| Column | Meaning |
|---|---|
| `label` | 1 if a qualifying bug-fix commit in `(T, T + horizon]` changed the file's content; 0 if none did; blank if the window is incomplete |
| `window_complete` | 1 when the whole window lies before the last observed commit |
| `label_fix_count`, `label_fix_commits` | How many qualifying fixes, and their short hashes (for auditing) |
| `deleted_in_window` | 1 if the file was deleted during the window |

The metadata JSON records:
- the column groups (`id_columns`, `feature_columns`, `label_columns`);
- the configuration and the repository's HEAD commit;
- snapshot dates, row and positive counts per snapshot;
- the label definition and known limitations.

### Leakage safeguards

- **Only past commits reach the feature code.** Commits are sorted by date and split at T; `compute_stats` receives just the commits dated at or before T. A test changes, adds and deletes future commits and asserts every feature column stays identical.
- **Files that appear after T are never rows.** Only files that exist at T are used.
- **`past_bug_fixes` counts only fixes dated at or before T.** Bug-fix classification looks at a single commit's own message and files, so classifying past commits uses no future information.
- **SZZ output is not used,** for features or for labels.
- **Label fields are kept apart.** They are listed separately in the CSV and the metadata; feature column names never contain "label" or "future", and a test checks the groups don't overlap.
- **No fake negatives.** A window that runs past the last observed commit is never labelled 0: the snapshot is dropped, or kept with a blank label if `--include-incomplete` is given.
- **Window edges are tested.** A fix exactly at T counts as past; one microsecond later it is future. The window end is inclusive.

### Renames, deletions and edge cases

| Case | Handling |
|---|---|
| Renamed **inside** the window | Followed: a fix to `new.py` after `old.py → new.py` labels the `old.py` row |
| Renamed **before** T | The row uses the new path. Features cover history since the rename only (history is split at renames) |
| Deleted before T | Not a row |
| Deleted inside the window | Row kept, `deleted_in_window = 1`; label reflects fixes before the deletion |
| A fix that only deletes a file, or a pure rename | Not a label event for that file (no content change) |
| Commit with no date | Skipped and counted in the metadata (`commits_skipped_missing_date`) |
| Naive (timezone-less) dates | Treated as UTC |
| Empty repository, or no snapshot fits | Empty CSV (header only), with a metadata note |
| Path that isn't a git repository | Error |

### Label limitations

- **Labels are heuristic.** A 1 means a commit that *looks like* a bug fix, based on its message and files, changed this file. It is not a verified defect.
  - Fixes with vague messages are missed, which creates false 0s.
  - Mislabelled commits create false 1s.
  - Measure the detector's precision on a hand-labelled sample before trusting results.
- **Every file a fix touches gets a positive,** including incidental ones (shared helpers, `__init__.py`).
- **Activity bias.** Busy eras and busy files produce more labels; on `psf/requests` the positive rate falls sharply in later years.
- **SZZ candidates are not proven causes of bugs,** which is why they are not part of the label.
- **Observation end is the last commit, not "today".** A repository that is simply quiet looks fully observed up to its last commit.
- **Merge commits are excluded** (as everywhere in RepoRisk), and history is replayed in committer-date order, which can differ from topological order on rebased or branchy history.

## Training and evaluation

`reporisk/model.py` trains and evaluates future bug-fix prediction on a dataset built with `--build-dataset`. It is a separate command, so the main CLI is unchanged:

```bash
python -m reporisk ~/test-repos/requests --build-dataset --exclude-tests \
    --snapshot-interval 30 --horizon 60 --dataset-out output/requests_30_60.csv
python -m reporisk.model output/requests_30_60.csv
```

| Option | Default | Effect |
|---|---|---|
| `--out-dir DIR` | `output/model` | Where `metrics.json` and `predictions.csv` go (git-ignored) |
| `--test-fraction F` | 0.25 | Share of the **latest snapshot dates** used as the test set |
| `--test-start DATE` | — | First test snapshot date; overrides `--test-fraction` |
| `--purge-days N` | horizon from metadata | Drop training rows whose label window reaches the test period. Required if there's no `.meta.json` |
| `--top-k K …` | `10 20 50` | K values for precision@K and recall@K |
| `--no-logreg` | off | Run only the churn baseline |
| `--metadata FILE` | `<dataset>.meta.json` | Metadata path |

### Dependencies

The data validation, chronological split, preprocessing, churn baseline and **all metrics** use only the Python standard library.

The logistic regression needs scikit-learn and is **optional**:

```bash
pip install -r requirements-ml.txt
```

`requirements-ml.txt` pins `scikit-learn>=1.3,<1.7`, because scikit-learn 1.7+ (and numpy 2.1+) require Python ≥ 3.10, and this project's venv uses Python 3.9. Without scikit-learn, the logistic regression is reported as `skipped: scikit-learn is not installed` and the baseline still runs. pandas is not used.

### Pipeline

1. **Load and validate.**
   - `snapshot_date`, `path` and `label` must exist.
   - Labels must be `0`, `1` or blank; blank or incomplete-window rows are dropped and counted.
   - Cells that are blank, non-numeric, NaN or inf become missing values.
2. **Select features.** Only columns in the code's allowlist (`reporisk/schema.py: FEATURE_COLUMNS`) are used, narrowed to the metadata's `feature_columns`.
   - Any column that is an ID, a label column, or whose name contains `label`, `future`, `window` or `deleted_in` is refused.
   - Metadata that lists such a column as a feature is an error.
   - `path` is not a feature: a raw file name would let the model memorise specific files instead of learning risk patterns.
3. **Split chronologically.**
   - The latest 25% of snapshot dates form the test set, and one date is never split across train and test.
   - **Purging:** training rows with `snapshot + purge_days > test start` are dropped, because their labels come from commits inside the test period.
4. **Preprocess,** fitted on training rows only:
   - median imputation;
   - `log1p` on count and day features (all except `author_hhi` and `strongest_coupling`);
   - standardization.
5. **Score the same test rows twice:**
   - **churn baseline:** rank by historical `churn`; missing churn ranks last;
   - **logistic regression:** class-balanced L2 (`class_weight="balanced"`, `random_state=0`).

   Both flag the same number of rows as positive: `round(training prevalence × test rows)`. That gives the two scorers an equal alert budget.
6. **Write** `metrics.json` and `predictions.csv`.
   - `metrics.json` holds the split details, features, warnings and per-model metrics, plus logistic-regression coefficients.
   - `predictions.csv` has one row per test row with both scores and ranks.
   - No model binary is saved.

### Metrics

| Metric | Notes |
|---|---|
| Precision, recall, F1, confusion matrix | At the shared alert budget. Logistic regression also reports them at probability 0.5 (`at_probability_0_5`) |
| PR-AUC | Average precision, step-wise like scikit-learn (verified against it in tests). Compare it to the test **prevalence**, which is what random ranking scores |
| ROC-AUC | Ties count half |
| Precision@K, Recall@K | Over all test rows pooled across test snapshots |
| Prevalence | Share of positive test rows |

**Undefined metrics are `null`, never 0:**
- precision when nothing is flagged;
- recall, PR-AUC and ROC-AUC when the test set has no positives (ROC-AUC also needs at least one negative);
- @K when K exceeds the number of rows.

A single-class split, or fewer than 10 positives in either split, produces a warning.

### First results on `psf/requests`

From `requests_30_60.csv`: 3,245 rows, 30-day snapshots, 60-day horizon. With the default split:

| | Train | Test |
|---|---|---|
| Snapshots | 2011-03 … 2022-07 | 2022-09 … 2026-06 |
| Rows | 2,331 | 895 |
| Positives | 219 (9.4%) | **6 (0.7%)** |

| Model | PR-AUC | ROC-AUC | Precision@10 |
|---|---|---|---|
| churn baseline | 0.015 | 0.552 | 0.0 |
| logistic regression | 0.010 | 0.593 | 0.0 |

**With only 6 positive test rows, these numbers cannot tell the models apart.** The project's late years are quiet, so the training prevalence (9.4%) is far above the test prevalence (0.7%), and both models flag 84 rows to find a handful of positives.

Exploratory splits with earlier test periods, chosen after seeing the default result (so not a tuned benchmark):

| Test from | Test positives | Churn PR-AUC | Logistic regression PR-AUC | Churn ROC-AUC | Logistic regression ROC-AUC |
|---|---|---|---|---|---|
| 2016-01-01 | 69 | 0.094 | 0.219 | 0.761 | 0.807 |
| 2019-01-01 | 32 | 0.064 | 0.114 | 0.731 | 0.772 |

These suggest the features carry signal beyond churn alone. A single repository and a single split are not enough to call that a reliable result.

### Labels vs. confirmed defects

The target is **"a commit that the heuristic detector classifies as a likely bug fix changed this file within the horizon"**. It is not "this file contained a confirmed defect".
- The detector misses fixes with vague messages, which become false negatives.
- It mislabels some commits, which become false positives.
- Every file a fix touched counts, including incidental ones.

A high score therefore means "resembles files that historically received fix-like commits". Before treating metrics as defect-prediction quality, hand-label a sample of fix commits to measure the detector's precision.

### Training limitations

- **Distribution shift over time.** Prevalence falls from about 23% (2011–15) to about 2% (2021–26) on `requests`, so a model trained on the busy years is miscalibrated for recent ones. Precision at a fixed budget suffers most.
- **Rows are correlated.** The same file appears in every snapshot, and with a 30-day interval and a 60-day horizon, label windows overlap. Purging guards the train/test boundary, but within each split the rows are not independent.
- **@K is pooled.** Precision@K is computed over all test snapshots together. A per-snapshot @K ("the top 10 files this month") would match day-to-day use better.
- **No hyperparameter tuning or calibration yet,** and no validation split inside the training period. Add one before tuning anything, so the test set stays untouched.
- **One repository.** Results from `requests` do not generalise to other projects.
