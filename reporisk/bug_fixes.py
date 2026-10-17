"""Heuristic detection of historical bug-fix commits.

Each commit is classified as `likely_bug_fix`, `uncertain` or
`not_likely_bug_fix` from its message and the kinds of files it changed.
These are heuristic labels, not ground truth: they will miss bug fixes with
vague messages and mislabel some commits that only sound like fixes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Iterable

from .filters import is_doc_file, is_test_file
from .mining import CommitRecord, FileChange, read_commits


class Outcome(str, Enum):
    LIKELY = "likely_bug_fix"
    UNCERTAIN = "uncertain"
    NOT_LIKELY = "not_likely_bug_fix"


# Regex fragments, matched case-insensitively against the subject line with
# word boundaries on both sides, so "fix" does not match "prefix" or "fixture"
# and "bug" does not match "debug".

# Terms that on their own point to a software defect.
STRONG_TERMS = (
    r"bugs?", r"bugfix(?:es)?", r"hotfix(?:es)?",
    r"regressions?", r"defects?",
    r"crash(?:es|ed|ing)?", r"segfault(?:s|ed)?",
    r"fix(?:es|ed)?\s+#\d+",
)

# Terms that suggest a fix but are common in non-bug commits too.
WEAK_TERMS = (
    r"fix(?:es|ed|ing)?", r"patch(?:es|ed)?", r"resolv(?:e|es|ed|ing)",
    r"incorrect(?:ly)?", r"broken", r"wrong",
    r"errors?", r"exceptions?", r"fail(?:s|ed|ing|ure|ures)?",
)

# Terms that mark typo, formatting, documentation or dependency work.
NON_BUG_TERMS = (
    r"typos?", r"spelling", r"misspell(?:ed|ing|ings)?", r"grammar",
    r"whitespace", r"formatt(?:ing|ed)", r"pep\s?8", r"flake8", r"lint(?:ing|er)?",
    r"isort", r"black", r"style", r"error\s+messages?",
    r"ci", r"chore",
    r"docs?", r"documentation", r"docstrings?", r"readme", r"changelog", r"comments?",
    r"links?",
    r"bump(?:s|ed)?", r"upgrade[sd]?", r"dependenc(?:y|ies)", r"requirements",
    r"vendor(?:ed)?", r"version\s+bump", r"release",
    r"merge\s+conflicts?", r"revert(?:s|ed)?",
)

# Issue references in the commit body. "Fixes #123" is strong evidence;
# "Closes #45" / "Resolves #45" are weak, because closed issues are often
# feature requests.
BODY_ISSUE_TERMS = (r"fix(?:es|ed)?\s+#\d+",)
BODY_WEAK_ISSUE_TERMS = (r"(?:resolv(?:e|es|ed)|clos(?:e|es|ed))\s+#\d+",)

# Files that only declare dependencies; a commit touching nothing else is a
# dependency update, not a bug fix.
DEPENDENCY_FILE_PATTERNS = (
    "requirements*.txt", "Pipfile", "Pipfile.lock", "poetry.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum",
)

# CI configuration; a commit touching nothing else fixes the pipeline, not the product.
CI_FILE_PATTERNS = (
    ".github/*", ".circleci/*", ".travis.yml", ".gitlab-ci.yml", "appveyor.yml",
    "azure-pipelines.yml", ".pre-commit-config.yaml",
)


@dataclass(frozen=True)
class BugFixRules:
    strong_terms: tuple[str, ...] = STRONG_TERMS
    weak_terms: tuple[str, ...] = WEAK_TERMS
    non_bug_terms: tuple[str, ...] = NON_BUG_TERMS
    body_issue_terms: tuple[str, ...] = BODY_ISSUE_TERMS
    body_weak_issue_terms: tuple[str, ...] = BODY_WEAK_ISSUE_TERMS
    dependency_file_patterns: tuple[str, ...] = DEPENDENCY_FILE_PATTERNS
    ci_file_patterns: tuple[str, ...] = CI_FILE_PATTERNS
    # How many different weak terms make a commit likely without a strong term.
    min_weak_terms_for_likely: int = 2


DEFAULT_RULES = BugFixRules()


@dataclass(frozen=True)
class BugFixCandidate:
    hash: str
    message: str
    timestamp: datetime
    files: tuple[FileChange, ...]
    outcome: Outcome
    evidence: tuple[str, ...] = field(default=())

    @property
    def subject(self) -> str:
        return _subject(self.message)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def deleted(self) -> int:
        return sum(f.deleted for f in self.files)


@lru_cache(maxsize=None)
def _compile(terms: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(rf"\b(?:{t})\b", re.IGNORECASE) for t in terms)


def _matches(terms: tuple[str, ...], text: str) -> list[str]:
    """One lowercase match per term that occurs in the text."""
    found = []
    for pattern in _compile(terms):
        m = pattern.search(text)
        if m:
            found.append(m.group(0).lower())
    return found


def _subject(message: str | None) -> str:
    return (message or "").strip().split("\n", 1)[0].strip()


def _body(message: str | None) -> str:
    parts = (message or "").strip().split("\n", 1)
    return parts[1] if len(parts) > 1 else ""


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    # fnmatch's "*" also matches "/", so ".github/*" covers nested workflow files.
    return any(fnmatch(path, pat) or PurePosixPath(path).match(pat) for pat in patterns)


def _file_kind(paths: Iterable[str], rules: BugFixRules) -> str | None:
    """'docs-only', 'dependency-only', 'ci-only' or 'tests-only' if every path is that kind."""
    paths = list(paths)
    if not paths:
        return None
    if all(is_doc_file(p) for p in paths):
        return "docs-only"
    if all(_matches_any(p, rules.dependency_file_patterns) for p in paths):
        return "dependency-only"
    if all(_matches_any(p, rules.ci_file_patterns) for p in paths):
        return "ci-only"
    if all(is_test_file(p) for p in paths):
        return "tests-only"
    return None


def classify(
    message: str | None, paths: Iterable[str], rules: BugFixRules = DEFAULT_RULES
) -> tuple[Outcome, tuple[str, ...]]:
    """Classify a commit from its message and changed paths.

    Keywords are matched in the subject line only, because bodies often
    quote release notes ("bug fixes") or unrelated discussion. The body is
    searched only for explicit issue references such as "Fixes #123".
    """
    subject, body = _subject(message), _body(message)
    strong = _matches(rules.strong_terms, subject) + _matches(rules.body_issue_terms, body)
    weak = _matches(rules.weak_terms, subject) + _matches(rules.body_weak_issue_terms, body)
    non_bug = _matches(rules.non_bug_terms, subject)
    kind = _file_kind(paths, rules)

    evidence = (
        [f"strong:{t}" for t in strong]
        + [f"weak:{t}" for t in weak]
        + [f"non-bug:{t}" for t in non_bug]
    )
    if kind:
        evidence.append(f"files:{kind}")

    if not strong and not weak:
        outcome = Outcome.NOT_LIKELY
        evidence.append("no bug-fix keywords")
    elif kind in ("docs-only", "dependency-only", "ci-only"):
        outcome = Outcome.NOT_LIKELY
    elif non_bug:
        # "Fix typo" is not a bug fix; "Fix typo and crash" is mixed.
        outcome = Outcome.UNCERTAIN if strong else Outcome.NOT_LIKELY
    elif strong or len(weak) >= rules.min_weak_terms_for_likely:
        outcome = Outcome.LIKELY
    else:
        outcome = Outcome.UNCERTAIN

    # Fixing only tests is usually a flaky or broken test, not a product bug.
    if kind == "tests-only" and outcome is Outcome.LIKELY:
        outcome = Outcome.UNCERTAIN

    return outcome, tuple(evidence)


def classify_commit(commit: CommitRecord, rules: BugFixRules = DEFAULT_RULES) -> BugFixCandidate:
    outcome, evidence = classify(commit.message, (c.path for c in commit.changes), rules)
    return BugFixCandidate(
        hash=commit.hash,
        message=commit.message,
        timestamp=commit.date,
        files=commit.changes,
        outcome=outcome,
        evidence=evidence,
    )


def detect_bug_fixes(
    commits: Iterable[CommitRecord],
    rules: BugFixRules = DEFAULT_RULES,
    cutoff: datetime | None = None,
) -> list[BugFixCandidate]:
    """Classify every commit dated at or before `cutoff` (all commits if None)."""
    return [
        classify_commit(c, rules) for c in commits if cutoff is None or c.date <= cutoff
    ]


def detect_in_repo(
    repo_path: str, rules: BugFixRules = DEFAULT_RULES, cutoff: datetime | None = None
) -> list[BugFixCandidate]:
    """Read a local repository with PyDriller and classify its commits."""
    return detect_bug_fixes(read_commits(repo_path), rules, cutoff)
