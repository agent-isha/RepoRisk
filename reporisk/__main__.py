import argparse
import sys
from pathlib import Path

from .mining import RECENT_WINDOWS_DAYS, analyze_repo, parse_cutoff, rank_files


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
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        sys.exit(f"Not a git repository: {repo}")

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


if __name__ == "__main__":
    main()
