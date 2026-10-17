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
