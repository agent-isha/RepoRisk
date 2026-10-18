"""Transparent heuristic risk levels for files, separate from any ML model.

A file gets one risk factor per rule below that it meets. Percentile rules
compare a file with the other files in the same analysis, so the thresholds
adapt to the repository's size. The level is just the factor count:

    high   = 3 or more factors
    medium = 1 or 2 factors
    low    = no factors

These are indicators for review, not predictions and not verified defects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from .mining import FileStats

HIGH_PERCENTILE = 90  # "top 10% of files"
MIN_BUG_FIX_COMMITS = 2
CONCENTRATION_MIN_COMMITS = 10
CONCENTRATION_MIN_HHI = 0.75
COUPLING_MIN_COMMITS = 5
COUPLING_MIN_RATIO = 0.5
HIGH_MIN_FACTORS = 3


@dataclass(frozen=True)
class FactorDefinition:
    code: str
    label: str
    rule: str


FACTOR_DEFINITIONS = (
    FactorDefinition("high_churn", "High churn",
                     f"Total lines added + deleted is in the top {100 - HIGH_PERCENTILE}% of files"),
    FactorDefinition("frequent_changes", "Frequent changes",
                     f"Commit count is in the top {100 - HIGH_PERCENTILE}% of files"),
    FactorDefinition("recent_churn", "Recent churn",
                     f"Churn in the last 90 days is above zero and in the top "
                     f"{100 - HIGH_PERCENTILE}% of files"),
    FactorDefinition("bug_fix_history", "Bug-fix history",
                     f"Touched by at least {MIN_BUG_FIX_COMMITS} commits classified as likely bug fixes"),
    FactorDefinition("concentrated_ownership", "Concentrated ownership",
                     f"At least {CONCENTRATION_MIN_COMMITS} commits and author HHI >= "
                     f"{CONCENTRATION_MIN_HHI} (mostly one author)"),
    FactorDefinition("strong_coupling", "Strong coupling",
                     f"At least {COUPLING_MIN_COMMITS} commits and >= {COUPLING_MIN_RATIO:.0%} of "
                     "them also change one partner file"),
)
LEVEL_DEFINITIONS = (
    ("high", f"{HIGH_MIN_FACTORS} or more risk factors"),
    ("medium", f"1 to {HIGH_MIN_FACTORS - 1} risk factors"),
    ("low", "no risk factors"),
)
_LABELS = {d.code: d.label for d in FACTOR_DEFINITIONS}


@dataclass(frozen=True)
class RiskFactor:
    code: str
    label: str
    detail: str


@dataclass(frozen=True)
class FileRisk:
    path: str
    level: str
    factors: tuple[RiskFactor, ...]


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; 0 for an empty sequence."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def level_for(count: int) -> str:
    if count >= HIGH_MIN_FACTORS:
        return "high"
    return "medium" if count else "low"


def assess(
    files: Mapping[str, FileStats], bug_fix_commits: Mapping[str, int]
) -> dict[str, FileRisk]:
    """Risk factors and level for each file, relative to the other files given."""
    stats = list(files.values())
    churn_cut = percentile([s.churn for s in stats], HIGH_PERCENTILE)
    commit_cut = percentile([s.commits for s in stats], HIGH_PERCENTILE)
    recent_cut = percentile([s.recent_churn[90] for s in stats], HIGH_PERCENTILE)

    result = {}
    for path, s in files.items():
        factors = []

        def add(code: str, detail: str) -> None:
            factors.append(RiskFactor(code, _LABELS[code], detail))

        if s.churn > 0 and s.churn >= churn_cut:
            add("high_churn", f"{s.churn} lines changed (top-10% threshold: {churn_cut:g})")
        if s.commits > 0 and s.commits >= commit_cut:
            add("frequent_changes", f"{s.commits} commits (top-10% threshold: {commit_cut:g})")
        recent = s.recent_churn[90]
        if recent > 0 and recent >= recent_cut:
            add("recent_churn", f"{recent} lines changed in the last 90 days")
        fixes = bug_fix_commits.get(path, 0)
        if fixes >= MIN_BUG_FIX_COMMITS:
            add("bug_fix_history", f"{fixes} likely bug-fix commits touched this file")
        hhi = s.author_hhi
        if s.commits >= CONCENTRATION_MIN_COMMITS and hhi is not None and hhi >= CONCENTRATION_MIN_HHI:
            add("concentrated_ownership", f"author HHI {hhi:.2f} over {s.commits} commits")
        if s.commits >= COUPLING_MIN_COMMITS and s.strongest_coupling >= COUPLING_MIN_RATIO:
            add("strong_coupling",
                f"{s.strongest_coupling:.0%} of commits also change {s.top_partner}")
        result[path] = FileRisk(path, level_for(len(factors)), tuple(factors))
    return result
