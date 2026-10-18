"""Leakage-safe, file-level dataset for predicting future bug-fix activity.

Each row is (snapshot date T, file). Features use only commits dated at or
before T. The label says whether a commit in (T, T + horizon] that the
heuristic bug-fix detector marks as a qualifying fix changed the file.

Labels are heuristic: the detector misses fixes and mislabels some commits,
so a 1 means "a commit that looks like a bug fix touched this file", not a
verified defect. SZZ candidates are not used here.
"""

from __future__ import annotations

import bisect
import csv
import json
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .bug_fixes import DEFAULT_RULES, BugFixRules, Outcome, classify_commit
from .filters import is_relevant
from .mining import CommitRecord, compute_stats, read_commits

DEFAULT_HORIZON_DAYS = 90
# Equal to the horizon by default, so label windows of consecutive snapshots
# don't overlap and the same future fix isn't counted in several snapshots.
DEFAULT_SNAPSHOT_INTERVAL_DAYS = 90

# Rows are limited to source files by default. Files such as AUTHORS.rst,
# .gitignore or setup.cfg are often touched by bug-fix commits without being
# where the bug was, which would add misleading positive labels.
SOURCE_EXTENSIONS = (
    ".py", ".pyx", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".java", ".kt", ".scala",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs", ".go", ".rs", ".rb",
    ".php", ".swift", ".m", ".mm", ".dart", ".lua", ".pl", ".sh", ".vue", ".svelte",
)

ID_COLUMNS = ("snapshot_date", "path")
# Model inputs. Every value is computed from commits dated <= the snapshot.
FEATURE_COLUMNS = (
    "commits",
    "lines_added",
    "lines_deleted",
    "churn",
    "churn_30d",
    "churn_90d",
    "churn_180d",
    "authors",
    "author_hhi",
    "coupled_files",
    "strongest_coupling",
    "age_days",
    "days_since_last_change",
    "past_bug_fixes",
)
# Derived from commits after the snapshot. Never use these as model inputs.
LABEL_COLUMNS = (
    "label",
    "window_complete",
    "label_fix_count",
    "label_fix_commits",
    "deleted_in_window",
)
COLUMNS = ID_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS


@dataclass(frozen=True)
class DatasetConfig:
    horizon_days: int = DEFAULT_HORIZON_DAYS
    snapshot_interval_days: int = DEFAULT_SNAPSHOT_INTERVAL_DAYS
    # Default: one interval after the first commit's date, so every snapshot
    # has some history behind it.
    first_snapshot: date | None = None
    # Last instant whose history is known. Default: the latest commit date.
    # Commits after it are ignored entirely.
    observation_end: datetime | None = None
    label_outcomes: tuple[Outcome, ...] = (Outcome.LIKELY,)
    # Keep snapshots whose label window runs past observation_end, with a
    # blank label instead of a fake 0.
    include_incomplete: bool = False
    include_tests: bool = True
    include_docs: bool = False
    extra_excludes: tuple[str, ...] = ()
    # None keeps every file type that passes the filters.
    source_extensions: tuple[str, ...] | None = SOURCE_EXTENSIONS
    rules: BugFixRules = DEFAULT_RULES

    def is_sample(self, path: str) -> bool:
        """Whether a file becomes a row (before the alive-at-snapshot check)."""
        if self.source_extensions is not None and not path.lower().endswith(self.source_extensions):
            return False
        return is_relevant(
            path,
            include_tests=self.include_tests,
            include_docs=self.include_docs,
            extra_patterns=self.extra_excludes,
        )

    def __post_init__(self):
        if self.horizon_days <= 0:
            raise ValueError("horizon_days must be positive")
        if self.snapshot_interval_days <= 0:
            raise ValueError("snapshot_interval_days must be positive")
        if not self.label_outcomes:
            raise ValueError("label_outcomes must not be empty")


@dataclass
class Dataset:
    rows: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def end_of_day(d: date) -> datetime:
    """The last instant of a calendar day in UTC, matching --cutoff."""
    return datetime.combine(d, time.max, tzinfo=timezone.utc)


def _normalize(commits: Iterable[CommitRecord]) -> tuple[list[CommitRecord], int]:
    """Drop commits without a date, treat naive dates as UTC, sort by date."""
    kept, missing = [], 0
    for c in commits:
        if c.date is None:
            missing += 1
            continue
        if c.date.tzinfo is None:
            c = CommitRecord(c.hash, c.author, c.date.replace(tzinfo=timezone.utc),
                             c.changes, c.message)
        kept.append(c)
    kept.sort(key=lambda c: c.date)  # stable: keeps traversal order for equal dates
    return kept, missing


def alive_files(commits: Iterable[CommitRecord]) -> set[str]:
    """Paths that exist after replaying the commits' adds, renames and deletes."""
    alive: set[str] = set()
    for c in commits:
        for ch in c.changes:
            if ch.change_type == "DELETE":
                alive.discard(ch.path)
            elif ch.change_type == "RENAME" and ch.old_path:
                alive.discard(ch.old_path)
                alive.add(ch.path)
            else:
                alive.add(ch.path)
    return alive


def _touches(commit: CommitRecord) -> set[str]:
    """Paths whose content a commit changed (deletions and pure renames excluded)."""
    return {
        ch.path for ch in commit.changes
        if ch.change_type != "DELETE" and ch.added + ch.deleted > 0
    }


def _future_labels(
    future: list[CommitRecord], samples: set[str], fix_hashes: set[str]
) -> dict[str, dict]:
    """Label each sample path from commits in its future window.

    Renames are followed, so a fix to `new.py` after `old.py -> new.py`
    labels the sample `old.py`. A path added inside the window is a new file,
    not a continuation of any sample.
    """
    origin = {p: p for p in samples}  # current path -> path at the snapshot
    labels = {p: {"count": 0, "commits": [], "deleted": False} for p in samples}
    for c in future:
        for ch in c.changes:
            if ch.change_type == "RENAME" and ch.old_path in origin:
                origin[ch.path] = origin.pop(ch.old_path)
            elif ch.change_type == "DELETE" and ch.path in origin:
                labels[origin.pop(ch.path)]["deleted"] = True
            elif ch.change_type == "ADD":
                origin.pop(ch.path, None)
        if c.hash in fix_hashes:
            for sample in {origin[p] for p in _touches(c) if p in origin}:
                labels[sample]["count"] += 1
                labels[sample]["commits"].append(c.hash[:12])
    return labels


def snapshot_schedule(
    first_commit: datetime, observation_end: datetime, config: DatasetConfig
) -> tuple[list[date], list[date]]:
    """(complete, incomplete) snapshot dates; incomplete windows end after observation_end."""
    step = timedelta(days=config.snapshot_interval_days)
    horizon = timedelta(days=config.horizon_days)
    current = config.first_snapshot or (first_commit.astimezone(timezone.utc).date() + step)
    complete, incomplete = [], []
    while end_of_day(current) <= observation_end:
        if end_of_day(current) + horizon <= observation_end:
            complete.append(current)
        else:
            incomplete.append(current)
        current += step
    return complete, incomplete


def _round(value):
    return round(value, 6) if isinstance(value, float) else value


def build_dataset(commits: Iterable[CommitRecord], config: DatasetConfig = DatasetConfig()) -> Dataset:
    """Build rows for every snapshot from in-memory commits."""
    commits, missing_dates = _normalize(commits)
    observation_end = config.observation_end
    if observation_end is not None and observation_end.tzinfo is None:
        observation_end = observation_end.replace(tzinfo=timezone.utc)
    if observation_end is not None:
        commits = [c for c in commits if c.date <= observation_end]
    elif commits:
        observation_end = commits[-1].date

    metadata = {
        "config": _config_dict(config),
        "commits_used": len(commits),
        "commits_skipped_missing_date": missing_dates,
        "observation_start": commits[0].date.isoformat() if commits else None,
        "observation_end": observation_end.isoformat() if observation_end else None,
        "id_columns": list(ID_COLUMNS),
        "feature_columns": list(FEATURE_COLUMNS),
        "label_columns": list(LABEL_COLUMNS),
    }
    if not commits:
        metadata.update(snapshots_complete=[], snapshots_incomplete=[], rows=0,
                        positives=0, note="no dated commits; dataset is empty")
        return Dataset([], metadata)

    complete, incomplete = snapshot_schedule(commits[0].date, observation_end, config)
    fix_hashes = {
        c.hash for c in commits
        if classify_commit(c, config.rules).outcome in config.label_outcomes
    }
    dates = [c.date for c in commits]
    horizon = timedelta(days=config.horizon_days)
    snapshots = [(d, True) for d in complete]
    if config.include_incomplete:
        snapshots += [(d, False) for d in incomplete]

    rows: list[dict] = []
    per_snapshot = []
    for snap, window_complete in sorted(snapshots):
        cutoff = end_of_day(snap)
        split = bisect.bisect_right(dates, cutoff)
        past = commits[:split]  # only commits dated <= cutoff reach feature code
        stats = compute_stats(past, cutoff).files
        past_fixes = Counter(p for c in past if c.hash in fix_hashes for p in _touches(c))
        samples = {p for p in alive_files(past) if p in stats and config.is_sample(p)}
        future = [c for c in commits[split:] if c.date <= cutoff + horizon]
        labels = _future_labels(future, samples, fix_hashes)

        positives = 0
        for path in sorted(samples):
            s, lab = stats[path], labels[path]
            row = {
                "snapshot_date": snap.isoformat(),
                "path": path,
                "commits": s.commits,
                "lines_added": s.added,
                "lines_deleted": s.deleted,
                "churn": s.churn,
                "churn_30d": s.recent_churn[30],
                "churn_90d": s.recent_churn[90],
                "churn_180d": s.recent_churn[180],
                "authors": len(s.authors),
                "author_hhi": _round(s.author_hhi),
                "coupled_files": s.coupled_files,
                "strongest_coupling": _round(s.strongest_coupling),
                "age_days": (cutoff - s.first_seen).days,
                "days_since_last_change": (cutoff - s.last_changed).days,
                "past_bug_fixes": past_fixes[path],
                "window_complete": int(window_complete),
                "deleted_in_window": int(lab["deleted"]),
            }
            if window_complete:
                row["label"] = int(lab["count"] > 0)
                row["label_fix_count"] = lab["count"]
                row["label_fix_commits"] = ";".join(lab["commits"])
                positives += row["label"]
            else:  # unobserved future: no label rather than a false 0
                row["label"] = row["label_fix_count"] = row["label_fix_commits"] = None
            rows.append(row)
        per_snapshot.append({
            "snapshot_date": snap.isoformat(),
            "window_complete": window_complete,
            "rows": len(samples),
            "positives": positives if window_complete else None,
        })

    labelled = [r for r in rows if r["label"] is not None]
    metadata.update(
        snapshots_complete=[d.isoformat() for d in complete],
        snapshots_incomplete=[d.isoformat() for d in incomplete],
        snapshots_incomplete_included=config.include_incomplete,
        qualifying_fix_commits=len(fix_hashes),
        rows=len(rows),
        labelled_rows=len(labelled),
        positives=sum(r["label"] for r in labelled),
        positive_rate=_round(sum(r["label"] for r in labelled) / len(labelled)) if labelled else None,
        distinct_files=len({r["path"] for r in rows}),
        per_snapshot=per_snapshot,
    )
    return Dataset(rows, metadata)


def _config_dict(config: DatasetConfig) -> dict:
    data = asdict(config)
    data["label_outcomes"] = [o.value for o in config.label_outcomes]
    data["first_snapshot"] = config.first_snapshot.isoformat() if config.first_snapshot else None
    data["observation_end"] = config.observation_end.isoformat() if config.observation_end else None
    data["extra_excludes"] = list(config.extra_excludes)
    data["source_extensions"] = (
        list(config.source_extensions) if config.source_extensions is not None else None
    )
    data.pop("rules")  # term lists are long; the rules are the BugFixRules defaults unless changed
    data["custom_bug_fix_rules"] = config.rules != DEFAULT_RULES
    return data


def _check_repo(repo_path: str) -> Path:
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError(f"not a git repository: {repo}")
    return repo


def _head(repo: Path) -> str | None:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() or None


def build_dataset_for_repo(repo_path: str, config: DatasetConfig = DatasetConfig()) -> Dataset:
    """Read a local repository (read-only) and build its dataset."""
    repo = _check_repo(repo_path)
    dataset = build_dataset(read_commits(str(repo)), config)
    dataset.metadata = {
        "repository": repo.name,
        "head_commit": _head(repo),
        **dataset.metadata,
    }
    return dataset


def write_dataset(
    dataset: Dataset, csv_path: str, repo_path: str | None = None
) -> tuple[Path, Path]:
    """Write the CSV and a `<name>.meta.json` beside it; returns both paths.

    Refuses to write inside `repo_path`, so the analyzed repository is never modified.
    """
    out = Path(csv_path).expanduser().resolve()
    if repo_path is not None:
        repo = Path(repo_path).expanduser().resolve()
        if out == repo or repo in out.parents:
            raise ValueError(f"refusing to write inside the analyzed repository: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    meta_path = out.with_name(out.stem + ".meta.json")

    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in dataset.rows:
            writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in COLUMNS})

    metadata = {
        **dataset.metadata,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "csv_file": out.name,
        "label_definition": (
            "1 if a commit dated in (snapshot end-of-day UTC, + horizon_days] that the "
            "heuristic bug-fix detector classifies with one of label_outcomes changed the "
            "file (renames followed); 0 otherwise; blank when the window is incomplete"
        ),
        "limitations": [
            "Labels are heuristic bug-fix classifications from commit messages, not verified defects.",
            "SZZ candidates are not used and are not proven causes of bugs.",
            "File history is split at renames: features of a renamed file start at the rename.",
            "Rows of the same file across snapshots are correlated; split train/test by snapshot date.",
            "Merge commits are excluded, so changes made only in merges are not seen.",
        ],
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
        f.write("\n")
    return out, meta_path
