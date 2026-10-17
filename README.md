# RepoRisk

Mines a local git repository's history and ranks files by risk signals: churn, recent activity, ownership concentration and change coupling.

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
