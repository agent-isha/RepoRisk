from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from .bug_fixes import Outcome, detect_in_repo
from .dataset import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_SNAPSHOT_INTERVAL_DAYS,
    SOURCE_EXTENSIONS,
    DatasetConfig,
    build_dataset_for_repo,
    write_dataset,
)
from .mining import RECENT_WINDOWS_DAYS, analyze_repo, parse_cutoff, rank_files
from .szz import trace_repo, write_jsonl


DEFAULT_DATASET_OUT = "output/dataset.csv"


def _cutoff_arg(value: str):
    try:
        return parse_cutoff(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date: {value!r} (use YYYY-MM-DD)")


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date: {value!r} (use YYYY-MM-DD)")


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive whole number of days: {value!r}")
    return number


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
    ds = parser.add_argument_group("dataset building (with --build-dataset)")
    ds.add_argument(
        "--build-dataset",
        action="store_true",
        help="instead of the ranking, build a leakage-safe file-level CSV dataset for "
        "future bug-fix prediction; --cutoff sets the end of observed history, and "
        "--exclude-tests/--include-docs/--exclude choose which files become rows",
    )
    ds.add_argument(
        "--dataset-out",
        metavar="FILE.csv",
        help=f"CSV output path (default: {DEFAULT_DATASET_OUT}); metadata is written "
        "beside it as <name>.meta.json",
    )
    ds.add_argument(
        "--snapshot-interval",
        type=_positive_int,
        metavar="DAYS",
        help=f"days between snapshots (default: {DEFAULT_SNAPSHOT_INTERVAL_DAYS})",
    )
    ds.add_argument(
        "--horizon",
        type=_positive_int,
        metavar="DAYS",
        help=f"future prediction window in days (default: {DEFAULT_HORIZON_DAYS})",
    )
    ds.add_argument(
        "--first-snapshot",
        type=_date_arg,
        metavar="DATE",
        help="first snapshot date, YYYY-MM-DD (default: one interval after the first commit)",
    )
    ds.add_argument(
        "--label-uncertain",
        action="store_true",
        help="also count 'uncertain' bug-fix commits as positive label events",
    )
    ds.add_argument(
        "--all-file-types",
        action="store_true",
        help="make rows for every file type, not only source code (.py, .js, .java, ...)",
    )
    ds.add_argument(
        "--include-incomplete",
        action="store_true",
        help="keep snapshots whose future window is not fully observed, with blank labels",
    )
    args = parser.parse_args()
    if sum([args.szz, args.bug_fixes, args.build_dataset]) > 1:
        parser.error("--szz, --bug-fixes and --build-dataset cannot be combined")
    if args.szz_output and not args.szz:
        parser.error("--szz-output requires --szz")
    dataset_only = {
        "--dataset-out": args.dataset_out,
        "--snapshot-interval": args.snapshot_interval,
        "--horizon": args.horizon,
        "--first-snapshot": args.first_snapshot,
        "--label-uncertain": args.label_uncertain,
        "--include-incomplete": args.include_incomplete,
        "--all-file-types": args.all_file_types,
    }
    used = [name for name, value in dataset_only.items() if value not in (None, False)]
    if used and not args.build_dataset:
        parser.error(f"{', '.join(used)} requires --build-dataset")

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        sys.exit(f"Not a git repository: {repo}")

    if args.build_dataset:
        build_and_write_dataset(str(repo), args)
        return
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


def build_and_write_dataset(repo: str, args: argparse.Namespace) -> None:
    config = DatasetConfig(
        horizon_days=args.horizon or DEFAULT_HORIZON_DAYS,
        snapshot_interval_days=args.snapshot_interval or DEFAULT_SNAPSHOT_INTERVAL_DAYS,
        first_snapshot=args.first_snapshot,
        observation_end=args.cutoff,
        label_outcomes=(Outcome.LIKELY, Outcome.UNCERTAIN) if args.label_uncertain
        else (Outcome.LIKELY,),
        include_incomplete=args.include_incomplete,
        include_tests=not args.exclude_tests,
        include_docs=args.include_docs,
        extra_excludes=tuple(args.exclude),
        source_extensions=None if args.all_file_types else SOURCE_EXTENSIONS,
    )
    dataset = build_dataset_for_repo(repo, config)
    try:
        csv_path, meta_path = write_dataset(dataset, args.dataset_out or DEFAULT_DATASET_OUT, repo)
    except ValueError as exc:
        sys.exit(str(exc))

    m = dataset.metadata
    print(f"Commits used: {m['commits_used']}   observation end: {m['observation_end']}")
    print(f"Snapshots: {len(m['snapshots_complete'])} complete, "
          f"{len(m['snapshots_incomplete'])} with incomplete future window "
          f"({'included, blank labels' if config.include_incomplete else 'dropped'})")
    print(f"Horizon: {config.horizon_days} days   interval: {config.snapshot_interval_days} days")
    print(f"Rows: {m['rows']}   labelled: {m.get('labelled_rows', 0)}   "
          f"positives: {m['positives']}   positive rate: {m.get('positive_rate')}")
    print(f"Wrote {csv_path}\n      {meta_path}")
    print("Labels are heuristic bug-fix classifications, not verified defects.")


if __name__ == "__main__":
    main()
