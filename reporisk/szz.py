"""SZZ-style tracing from bug-fix commits back to candidate bug-inducing commits.

For each bug-fix commit, the lines it deleted or changed are taken to be
the faulty lines. `git blame` on the fix's parent finds the commit that last
touched each of those lines, and that commit becomes a *candidate*
bug-inducing commit.

These are candidates, not proof. Blame finds the last change to a line, which
may be a refactor, a reformat or a move rather than the change that introduced
the bug. Bugs fixed only by adding lines cannot be traced at all.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from pydriller import Git, ModificationType

from .bug_fixes import DEFAULT_RULES, BugFixRules, Outcome, detect_in_repo
from .filters import is_doc_file, is_noise, is_test_file

# Line prefixes treated as comments when skip_comment_lines is on. "#" also
# matches C preprocessor lines such as "#include"; remove it for C/C++ repos.
COMMENT_PREFIXES = ("#", "//", "/*", "*", "*/", '"""', "'''", "--")

# Lines that are only syntax carry no information about a bug: blaming ")" or
# "else:" just finds whoever last reformatted the block.
TRIVIAL_LINES = frozenset({
    "try:", "else:", "finally:", "except:", "pass", "break", "continue", "return",
    "end", "do", "then", "fi", "default:",
})

BLAME_LIMITATION = (
    "git blame finds the last commit to change the line, which may not be "
    "the commit that introduced the bug"
)


@dataclass(frozen=True)
class SZZConfig:
    # Deleted lines in test and documentation files say little about where a
    # product bug came from, so they are not traced by default.
    trace_test_files: bool = False
    trace_doc_files: bool = False
    trace_noise_files: bool = False
    ignore_whitespace: bool = True  # git blame -w
    skip_blank_lines: bool = True
    skip_comment_lines: bool = True
    comment_prefixes: tuple[str, ...] = COMMENT_PREFIXES
    # Skip lines with no letters or digits (")", "});") and lone keywords ("try:").
    skip_trivial_lines: bool = True
    trivial_lines: frozenset[str] = TRIVIAL_LINES
    # Files where the fix deletes more lines than this look like rewrites or
    # moves rather than targeted corrections, and are skipped.
    max_deleted_lines_per_file: int = 100


DEFAULT_CONFIG = SZZConfig()


@dataclass(frozen=True)
class LineEvidence:
    fix_parent_line: int  # line number in the file just before the fix
    inducing_line: int  # line number in the file as of the inducing commit
    content: str


@dataclass(frozen=True)
class TraceRecord:
    fix_hash: str
    inducing_hash: str
    inducing_date: datetime | None
    path: str  # path at the fix's parent, where the deleted lines lived
    lines: tuple[LineEvidence, ...]
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["inducing_date"] = self.inducing_date.isoformat() if self.inducing_date else None
        return data


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str


@dataclass
class TraceResult:
    fix_hash: str
    records: list[TraceRecord] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    error: str | None = None  # why the whole commit could not be traced

    @property
    def inducing_hashes(self) -> set[str]:
        return {r.inducing_hash for r in self.records}


@dataclass(frozen=True)
class _BlameLine:
    sha: str
    orig_line: int
    final_line: int
    committer_time: datetime | None
    boundary: bool


def _skip_reason(path: str, config: SZZConfig) -> str | None:
    if not config.trace_noise_files and is_noise(path):
        return "noise file (lock, generated, vendored or binary asset)"
    if not config.trace_doc_files and is_doc_file(path):
        return "documentation file"
    if not config.trace_test_files and is_test_file(path):
        return "test file"
    return None


def _is_ignorable_line(content: str, config: SZZConfig) -> bool:
    stripped = content.strip()
    if not stripped:
        return config.skip_blank_lines
    if config.skip_comment_lines and stripped.startswith(config.comment_prefixes):
        return True
    if config.skip_trivial_lines and (
        not any(ch.isalnum() for ch in stripped) or stripped in config.trivial_lines
    ):
        return True
    return False


def _line_ranges(numbers: list[int]) -> list[tuple[int, int]]:
    """Collapse sorted line numbers into inclusive (start, end) ranges."""
    ranges: list[tuple[int, int]] = []
    for n in sorted(numbers):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


def _parse_time(epoch: str, tz: str) -> datetime:
    sign = -1 if tz.startswith("-") else 1
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    return datetime.fromtimestamp(int(epoch), timezone(offset))


def _blame(
    repo_path: str, rev: str, path: str, lines: list[int], ignore_whitespace: bool
) -> dict[int, _BlameLine]:
    """Run git blame on selected lines of `path` at `rev`, keyed by line number at `rev`."""
    cmd = ["git", "-C", repo_path, "blame", "--line-porcelain"]
    if ignore_whitespace:
        cmd.append("-w")
    for start, end in _line_ranges(lines):
        cmd += ["-L", f"{start},{end}"]
    cmd += [rev, "--", path]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, errors="replace").stdout

    result: dict[int, _BlameLine] = {}
    header: list[str] = []
    info: dict[str, str] = {}
    for line in out.splitlines():
        if line.startswith("\t"):
            sha, orig, final = header[0], int(header[1]), int(header[2])
            time = (
                _parse_time(info["committer-time"], info["committer-tz"])
                if "committer-time" in info and "committer-tz" in info
                else None
            )
            result[final] = _BlameLine(sha, orig, final, time, "boundary" in info)
            header, info = [], {}
        elif not header:
            header = line.split()
        else:
            key, _, value = line.partition(" ")
            info[key] = value
    return result


def trace_fix(
    repo_path: str, fix_hash: str, config: SZZConfig = DEFAULT_CONFIG, git: Git | None = None
) -> TraceResult:
    """Trace one bug-fix commit back to candidate bug-inducing commits."""
    git = git or Git(repo_path)
    result = TraceResult(fix_hash)
    try:
        commit = git.get_commit(fix_hash)
        # PyDriller loads lazily, so an unknown hash only fails on first access.
        is_merge, parents = commit.merge, commit.parents
    except Exception as exc:  # unknown hash, corrupt object
        result.error = f"cannot read commit: {exc}"
        return result
    result.fix_hash = commit.hash
    if is_merge:
        result.error = "merge commit (not traced)"
        return result
    if not parents:
        result.error = "root commit has no parent to blame"
        return result
    parent = parents[0]

    for mf in commit.modified_files:
        path = mf.old_path or mf.new_path
        if mf.change_type is ModificationType.ADD:
            continue  # new file: nothing existed before to be faulty
        if mf.change_type is ModificationType.DELETE:
            result.skipped.append(SkippedFile(path, "file deleted by the fix"))
            continue
        reason = _skip_reason(path, config)
        if reason:
            result.skipped.append(SkippedFile(path, reason))
            continue
        if mf.diff.startswith("Binary files"):
            result.skipped.append(SkippedFile(path, "binary file"))
            continue

        deleted = [
            (n, text)
            for n, text in mf.diff_parsed["deleted"]
            if not _is_ignorable_line(text, config)
        ]
        if not deleted:
            continue  # only additions, blank or comment lines removed
        if len(deleted) > config.max_deleted_lines_per_file:
            result.skipped.append(
                SkippedFile(path, f"{len(deleted)} deleted lines exceed the limit "
                                  f"of {config.max_deleted_lines_per_file}")
            )
            continue

        try:
            blamed = _blame(repo_path, parent, path, [n for n, _ in deleted],
                            config.ignore_whitespace)
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "").strip().splitlines()
            result.skipped.append(
                SkippedFile(path, f"git blame failed: {message[0] if message else exc}")
            )
            continue

        by_inducing: dict[str, list[tuple[LineEvidence, _BlameLine]]] = {}
        for n, text in deleted:
            b = blamed.get(n)
            if b is None:
                continue
            by_inducing.setdefault(b.sha, []).append((LineEvidence(n, b.orig_line, text), b))

        for sha, items in by_inducing.items():
            limitations = [BLAME_LIMITATION]
            if config.ignore_whitespace:
                limitations.append("whitespace-only changes were ignored by blame")
            if mf.change_type is ModificationType.RENAME:
                limitations.append(f"file renamed in the fix to {mf.new_path}; old path traced")
            if any(b.boundary for _, b in items):
                limitations.append("boundary commit: history before it is unavailable")
            kind = "test file" if is_test_file(path) else (
                "documentation file" if is_doc_file(path) else "source file")
            evidence = (
                f"fix deleted or changed {len(items)} line(s) in this {kind}",
                f"git blame{' -w' if config.ignore_whitespace else ''} on {parent[:10]} "
                f"attributes them to {sha[:10]}",
            )
            result.records.append(
                TraceRecord(
                    fix_hash=commit.hash,
                    inducing_hash=sha,
                    inducing_date=items[0][1].committer_time,
                    path=path,
                    lines=tuple(e for e, _ in items),
                    evidence=evidence,
                    limitations=tuple(limitations),
                )
            )
    return result


def trace_fixes(
    repo_path: str, fix_hashes: Iterable[str], config: SZZConfig = DEFAULT_CONFIG
) -> list[TraceResult]:
    git = Git(repo_path)
    return [trace_fix(repo_path, h, config, git) for h in fix_hashes]


def trace_repo(
    repo_path: str,
    *,
    outcomes: tuple[Outcome, ...] = (Outcome.LIKELY,),
    rules: BugFixRules = DEFAULT_RULES,
    config: SZZConfig = DEFAULT_CONFIG,
    cutoff: datetime | None = None,
) -> list[TraceResult]:
    """Detect bug fixes (likely only, by default) and trace each one."""
    fixes = [c.hash for c in detect_in_repo(repo_path, rules, cutoff) if c.outcome in outcomes]
    return trace_fixes(repo_path, fixes, config)


def write_jsonl(results: Iterable[TraceResult], path: str) -> int:
    """Write one JSON object per trace record; returns the number written."""
    count = 0
    with open(path, "w") as f:
        for result in results:
            for record in result.records:
                f.write(json.dumps(record.to_dict()) + "\n")
                count += 1
    return count
