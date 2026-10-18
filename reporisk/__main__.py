from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .bug_fixes import Outcome, detect_in_repo
from .mining import RECENT_WINDOWS_DAYS, analyze_repo, parse_cutoff, rank_files
from .szz import trace_repo, write_jsonl


def _cutoff_arg(value: str):
    try:
        return parse_cutoff(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date: {value!r} (use YYYY-MM-DD)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a local git repository's history.")
    parser.add_argument("repo", help="path to a local git repository")
    parser.add_argument("--top", type=int, default=10, help="how many files to show")
    parser.add_argument(
        "--exclude-tests", action="store_true", help="leave test files out of the ranking"
    )
    parser.add_argument(
        "--include-docs",
        action="store_true",
        help="keep README/changelog files and docs/ directories in the ranking",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help='extra glob pattern to exclude, e.g. "docs/*" (repeatable)',
    )
    parser.add_argument(
        "--cutoff",
        type=_cutoff_arg,
        metavar="DATE",
        help="analyze history up to this date (YYYY-MM-DD, end of day UTC); "
        "default: the latest commit",
    )
    parser.add_argument(
        "--bug-fixes",
        action="store_true",
        help="instead of the ranking, summarize heuristic bug-fix commit candidates "
        "(--top sets how many examples to show per outcome)",
    )
    parser.add_argument(
        "--szz",
        action="store_true",
        help="instead of the ranking, trace likely bug-fix commits back to candidate "
        "bug-inducing commits (SZZ-style; --top sets how many examples to show)",
    )
    parser.add_argument(
        "--szz-output",
        metavar="FILE.jsonl",
        help="with --szz, also write one JSON record per trace to this file",
    )
    args = parser.parse_args()
    if args.szz and args.bug_fixes:
        parser.error("--szz and --bug-fixes cannot be combined")
    if args.szz_output and not args.szz:
        parser.error("--szz-output requires --szz")

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        sys.exit(f"Not a git repository: {repo}")

    if args.bug_fixes:
        print_bug_fix_summary(str(repo), args.cutoff, args.top)
        return
    if args.szz:
        print_szz_summary(str(repo), args.cutoff, args.top, args.szz_output)
        return

    analysis = analyze_repo(str(repo), args.cutoff)
    files = analysis.files
    ranked = rank_files(
        files,
        include_tests=not args.exclude_tests,
        include_docs=args.include_docs,
        extra_excludes=args.exclude,
    )
    print(
        f"Commits analyzed: {analysis.commit_count}   Files touched: {len(files)}"
        f"   After filtering: {len(ranked)}"
    )
    cutoff = analysis.cutoff.isoformat() if analysis.cutoff else "n/a (no commits)"
    print(f"Cutoff: {cutoff}")
    print(f"Commits skipped for coupling (too many files): {analysis.skipped_large_commits}\n")

    windows = "".join(f"{f'c{d}d':>7}" for d in RECENT_WINDOWS_DAYS)
    print(f"{'churn':>8} {'commits':>8} {'authors':>8}{windows}   hhi coupled  cpl  path")
    for path, s in ranked[: args.top]:
        recent = "".join(f"{s.recent_churn[d]:>7}" for d in RECENT_WINDOWS_DAYS)
        hhi = f"{s.author_hhi:.2f}" if s.author_hhi is not None else "   -"
        print(
            f"{s.churn:>8} {s.commits:>8} {len(s.authors):>8}{recent}"
            f"  {hhi} {s.coupled_files:>7} {s.strongest_coupling:.2f}  {path}"
        )


def print_bug_fix_summary(repo: str, cutoff, examples: int) -> None:
    candidates = detect_in_repo(repo, cutoff=cutoff)
    counts = Counter(c.outcome for c in candidates)
    print(f"Commits examined: {len(candidates)}")
    for outcome in Outcome:
        print(f"  {outcome.value:<20} {counts[outcome]:>6}")
    print("Heuristic labels from commit messages and changed files, not ground truth.")

    for outcome in (Outcome.LIKELY, Outcome.UNCERTAIN):
        shown = [c for c in candidates if c.outcome is outcome][-examples:]
        if not shown:
            continue
        print(f"\nMost recent {outcome.value} ({len(shown)} of {counts[outcome]}):")
        for c in reversed(shown):
            print(
                f"  {c.hash[:8]}  {c.timestamp:%Y-%m-%d}  {len(c.files):>3} files"
                f"  +{c.added}/-{c.deleted}  {c.subject[:72]}"
            )
            print(f"            evidence: {', '.join(c.evidence)}")


def print_szz_summary(repo: str, cutoff, examples: int, output: str | None) -> None:
    results = trace_repo(repo, cutoff=cutoff)
    records = [r for result in results for r in result.records]
    traced = [r for r in results if r.records]
    errors = Counter(r.error for r in results if r.error)
    skipped = Counter(s.reason.split(":")[0] for r in results for s in r.skipped)
    inducing = {r.inducing_hash for r in records}

    print(f"Likely bug-fix commits:          {len(results)}")
    print(f"  traced to >=1 earlier commit:  {len(traced)}")
    print(f"  no traceable deleted lines:    {len(results) - len(traced) - sum(errors.values())}")
    for reason, n in errors.items():
        print(f"  not traced ({reason}): {n}")
    print(f"Candidate bug-inducing commits:  {len(inducing)} distinct")
    print(f"Trace records (fix, inducing, file): {len(records)}")
    if skipped:
        print("Files skipped:")
        for reason, n in skipped.most_common():
            print(f"  {n:>5}  {reason}")
    print("Candidates come from git blame heuristics; they do not prove which commit caused a bug.")

    if output:
        written = write_jsonl(results, output)
        print(f"\nWrote {written} records to {output}")

    if records:
        print(f"\nExamples ({min(examples, len(records))} of {len(records)}):")
        for r in records[-examples:][::-1]:
            first = r.lines[0]
            date = f"{r.inducing_date:%Y-%m-%d}" if r.inducing_date else "unknown date"
            print(
                f"  fix {r.fix_hash[:8]} <- {r.inducing_hash[:8]}"
                f" ({date})  {r.path}  {len(r.lines)} line(s)"
            )
            print(f"      line {first.fix_parent_line}: {first.content.strip()[:80]}")


if __name__ == "__main__":
    main()
