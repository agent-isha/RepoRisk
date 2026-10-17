from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Iterable

from pydriller import Repository

from .filters import is_relevant

# Recent-churn windows, in days before the analysis cutoff.
RECENT_WINDOWS_DAYS = (30, 90, 180)

# Commits touching more files than this (bulk reformats, renames, vendoring)
# are left out of coupling: they would add O(n²) meaningless pairs.
MAX_FILES_PER_COMMIT_FOR_COUPLING = 50

# A partner file counts as "coupled" only after this many shared commits.
MIN_COCHANGES_FOR_COUPLING = 2


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    deleted: int


@dataclass(frozen=True)
class CommitRecord:
    hash: str
    author: str
    date: datetime  # timezone-aware committer date
    changes: tuple[FileChange, ...]
    message: str = ""


@dataclass
class FileStats:
    commits: int = 0
    added: int = 0
    deleted: int = 0
    author_commits: Counter = field(default_factory=Counter)
    first_seen: datetime | None = None
    last_changed: datetime | None = None
    recent_churn: dict[int, int] = field(
        default_factory=lambda: dict.fromkeys(RECENT_WINDOWS_DAYS, 0)
    )
    coupled_files: int = 0
    strongest_coupling: float = 0.0
    top_partner: str | None = None

    @property
    def churn(self) -> int:
        return self.added + self.deleted

    @property
    def authors(self) -> set[str]:
        return set(self.author_commits)

    @property
    def author_hhi(self) -> float | None:
        """Herfindahl-Hirschman Index of commit shares per author, in (0, 1].

        1.0 means one author made every commit; 1/n means n authors share
        commits evenly. None when the file has no recorded commits.
        """
        total = sum(self.author_commits.values())
        if total == 0:
            return None
        return sum((n / total) ** 2 for n in self.author_commits.values())


@dataclass
class Analysis:
    files: dict[str, FileStats]
    commit_count: int
    cutoff: datetime | None
    # Sparse co-change counts, only for pairs that actually changed together,
    # keyed (path_a, path_b) with path_a < path_b.
    pair_counts: Counter
    skipped_large_commits: int


def read_commits(repo_path: str) -> list[CommitRecord]:
    """Read every non-merge commit (oldest first) from a local repository."""
    records = []
    for commit in Repository(repo_path, only_no_merge=True).traverse_commits():
        changes = tuple(
            # new_path is None when a file is deleted
            FileChange(mf.new_path or mf.old_path, mf.added_lines, mf.deleted_lines)
            for mf in commit.modified_files
        )
        records.append(
            CommitRecord(
                commit.hash, commit.author.email, commit.committer_date, changes, commit.msg
            )
        )
    return records


def compute_stats(
    commits: Iterable[CommitRecord],
    cutoff: datetime | None = None,
    *,
    max_files_per_commit: int = MAX_FILES_PER_COMMIT_FOR_COUPLING,
    min_cochanges: int = MIN_COCHANGES_FOR_COUPLING,
) -> Analysis:
    """Compute raw per-file stats from commits dated at or before `cutoff`.

    The cutoff defaults to the latest commit date, so every commit is used.
    A naive cutoff is treated as UTC. Recent churn for an N-day window counts
    commits with cutoff - N days < date <= cutoff.
    """
    commits = list(commits)
    if cutoff is None and commits:
        cutoff = max(c.date for c in commits)
    if cutoff is not None and cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    window_starts = (
        {days: cutoff - timedelta(days=days) for days in RECENT_WINDOWS_DAYS} if cutoff else {}
    )
    files: dict[str, FileStats] = defaultdict(FileStats)
    pair_counts: Counter = Counter()
    commit_count = 0
    skipped_large = 0

    for commit in commits:
        if cutoff is not None and commit.date > cutoff:
            continue
        commit_count += 1
        for change in commit.changes:
            stats = files[change.path]
            stats.commits += 1
            stats.added += change.added
            stats.deleted += change.deleted
            stats.author_commits[commit.author] += 1
            if stats.first_seen is None:
                stats.first_seen = commit.date
            stats.last_changed = commit.date
            for days, start in window_starts.items():
                if commit.date > start:
                    stats.recent_churn[days] += change.added + change.deleted

        paths = sorted({change.path for change in commit.changes})
        if len(paths) > max_files_per_commit:
            skipped_large += 1
        else:
            pair_counts.update(combinations(paths, 2))

    _add_coupling_features(files, pair_counts, min_cochanges)
    return Analysis(dict(files), commit_count, cutoff, pair_counts, skipped_large)


def _add_coupling_features(
    files: dict[str, FileStats], pair_counts: Counter, min_cochanges: int
) -> None:
    """Set coupled_files, top_partner and strongest_coupling on each file."""
    best: dict[str, tuple[int, str]] = {}
    for (a, b), shared in pair_counts.items():
        if shared < min_cochanges:
            continue
        for path, partner in ((a, b), (b, a)):
            files[path].coupled_files += 1
            current = best.get(path)
            if current is None or shared > current[0] or (
                shared == current[0] and partner < current[1]
            ):
                best[path] = (shared, partner)

    for path, (shared, partner) in best.items():
        files[path].top_partner = partner
        files[path].strongest_coupling = shared / files[path].commits


def analyze_repo(repo_path: str, cutoff: datetime | None = None) -> Analysis:
    return compute_stats(read_commits(repo_path), cutoff)


def analyze(repo_path: str) -> tuple[dict[str, FileStats], int]:
    """Walk every non-merge commit and collect per-file stats."""
    analysis = analyze_repo(repo_path)
    return analysis.files, analysis.commit_count


def parse_cutoff(value: str) -> datetime:
    """Parse "YYYY-MM-DD" (end of that day, UTC) or an ISO datetime (naive = UTC)."""
    parsed = datetime.fromisoformat(value)
    if len(value) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def rank_files(
    files: dict[str, FileStats],
    *,
    include_tests: bool = True,
    include_docs: bool = False,
    extra_excludes: Iterable[str] = (),
) -> list[tuple[str, FileStats]]:
    """Return relevant files sorted by churn, highest first. `files` is not modified."""
    extra_excludes = tuple(extra_excludes)
    relevant = [
        (path, stats)
        for path, stats in files.items()
        if is_relevant(
            path,
            include_tests=include_tests,
            include_docs=include_docs,
            extra_patterns=extra_excludes,
        )
    ]
    return sorted(relevant, key=lambda kv: kv[1].churn, reverse=True)
