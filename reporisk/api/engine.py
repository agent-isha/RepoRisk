"""Run a full RepoRisk analysis by composing the existing engine modules.

Nothing here re-implements mining, filtering, bug-fix detection, SZZ, dataset
building or modelling; it reads the commits once and passes them through
those modules, then shapes the results for the API.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydriller import Git

from ..bug_fixes import Outcome, detect_bug_fixes
from ..dataset import SOURCE_EXTENSIONS, DatasetConfig, alive_files, build_dataset, write_dataset
from ..mining import compute_stats, rank_files, read_commits
from ..model import (
    LABEL_DISCLAIMER,
    DatasetError,
    LogisticRegression,
    fit_and_score,
    load_dataset,
    load_latest_snapshot,
    run_pipeline,
)
from ..risk import FACTOR_DEFINITIONS, LEVEL_DEFINITIONS, assess
from ..szz import trace_fix

log = logging.getLogger(__name__)

STAGES = (
    ("reading_history", "Reading git history"),
    ("computing_features", "Computing file features and risk factors"),
    ("detecting_bug_fixes", "Detecting bug-fix commits"),
    ("tracing_szz", "Tracing candidate bug-inducing commits"),
    ("building_dataset", "Building the prediction dataset"),
    ("training_model", "Training and evaluating the model"),
)

PREDICTION_LIMITATIONS = [
    "Probabilities come from a logistic regression trained on this repository's own history; "
    "they are not validated defect predictions.",
    "The target is 'a commit classified as a likely bug fix touches this file within the "
    "horizon', which includes missed and mislabelled fixes.",
    "Check the chronological evaluation before relying on the ranking; with few test "
    "positives the metrics are unstable.",
    "Scores describe the latest dataset snapshot, which can be up to one snapshot interval "
    "older than the latest commit.",
]
BUG_FIX_LIMITATIONS = [
    "Bug-fix outcomes are heuristic classifications of commit messages and changed files, "
    "not verified defects.",
    "SZZ candidates are the commits that last touched lines a fix changed. They do not prove "
    "which commit introduced a bug.",
    "Fixes that only add lines cannot be traced; merge commits are not analyzed.",
]

Progress = Callable[[str, str], None]


@dataclass
class AnalysisResult:
    summary: dict
    files: list[dict]
    bug_fixes: list[dict]
    predictions: dict


def _head(repo: str) -> str | None:
    try:
        return Git(repo).get_head().hash
    except Exception:  # empty repository: no HEAD yet
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def run_analysis(
    repo_path: str,
    repo_id: str,
    repo_name: str,
    options,
    work_dir: Path,
    progress: Progress = lambda stage, message: None,
) -> AnalysisResult:
    timings: dict[str, float] = {}
    warnings: list[str] = []
    clock = [time.monotonic(), None]

    def stage(name: str, message: str = "") -> None:
        now = time.monotonic()
        if clock[1] is not None:
            timings[clock[1]] = round(now - clock[0], 2)
        clock[0], clock[1] = now, name
        progress(name, message)

    # 1. history
    stage("reading_history")
    commits = read_commits(repo_path)
    head = _head(repo_path)

    # 2. features + heuristic risk
    stage("computing_features")
    analysis = compute_stats(commits)
    current = alive_files(commits)
    extensions = SOURCE_EXTENSIONS if options.source_only else None
    ranked = [
        (path, s) for path, s in rank_files(analysis.files, include_tests=not options.exclude_tests)
        if path in current and (extensions is None or path.lower().endswith(extensions))
    ]

    # 3. bug-fix detection
    stage("detecting_bug_fixes")
    candidates = detect_bug_fixes(commits)
    outcome_counts = Counter(c.outcome.value for c in candidates)
    fix_counts: Counter = Counter()
    for c in candidates:
        if c.outcome is Outcome.LIKELY:
            fix_counts.update({f.path for f in c.files if f.change_type != "DELETE"})
    risks = assess(dict(ranked), fix_counts)

    files = []
    for path, s in ranked:
        r = risks[path]
        files.append({
            "path": path,
            "risk_level": r.level,
            "risk_factors": [{"code": f.code, "label": f.label, "detail": f.detail} for f in r.factors],
            "churn": s.churn,
            "lines_added": s.added,
            "lines_deleted": s.deleted,
            "commits": s.commits,
            "authors": len(s.authors),
            "author_hhi": round(s.author_hhi, 6) if s.author_hhi is not None else None,
            "churn_30d": s.recent_churn[30],
            "churn_90d": s.recent_churn[90],
            "churn_180d": s.recent_churn[180],
            "coupled_files": s.coupled_files,
            "strongest_coupling": round(s.strongest_coupling, 6),
            "top_partner": s.top_partner,
            "bug_fix_commits": fix_counts.get(path, 0),
            "first_seen": _iso(s.first_seen),
            "last_changed": _iso(s.last_changed),
        })
    files.sort(key=lambda f: (-len(f["risk_factors"]), -f["churn"], f["path"]))

    # 4. SZZ for likely fixes
    likely = [c for c in candidates if c.outcome is Outcome.LIKELY]
    traces: dict[str, object] = {}
    szz = {"status": "skipped", "reason": "disabled in analysis options"}
    if options.run_szz:
        stage("tracing_szz", f"0 of {len(likely)} likely bug fixes")
        git = Git(repo_path)
        for i, c in enumerate(likely, start=1):
            traces[c.hash] = trace_fix(repo_path, c.hash, git=git)
            if i % 10 == 0 or i == len(likely):
                progress("tracing_szz", f"{i} of {len(likely)} likely bug fixes")
        records = [rec for t in traces.values() for rec in t.records]
        szz = {
            "status": "completed",
            "fixes_traced": len(traces),
            "fixes_with_candidates": sum(1 for t in traces.values() if t.records),
            "inducing_commits": len({rec.inducing_hash for rec in records}),
            "records": len(records),
        }

    bug_fixes = []
    for c in reversed([c for c in candidates if c.outcome is not Outcome.NOT_LIKELY]):  # newest first
        trace = traces.get(c.hash)
        if not options.run_szz:
            szz_status, note = "skipped", "SZZ tracing was disabled"
        elif c.outcome is not Outcome.LIKELY:
            szz_status, note = "not_traced", "only likely bug fixes are traced"
        elif trace.error:
            szz_status, note = "not_traced", trace.error
        else:
            szz_status = "traced"
            note = None if trace.records else "no deleted source lines to trace"
            if trace.skipped:
                skipped = Counter(s.reason.split(":")[0] for s in trace.skipped)
                note = "; ".join(filter(None, [note, "skipped files: " + ", ".join(
                    f"{n} {reason}" for reason, n in skipped.items())]))
        bug_fixes.append({
            "hash": c.hash,
            "subject": c.subject,
            "message": c.message,
            "timestamp": c.timestamp.isoformat(),
            "outcome": c.outcome.value,
            "evidence": list(c.evidence),
            "files": [{"path": f.path, "added": f.added, "deleted": f.deleted} for f in c.files],
            "added": c.added,
            "deleted": c.deleted,
            "szz_status": szz_status,
            "szz_note": note,
            "szz_records": [
                {
                    "inducing_hash": rec.inducing_hash,
                    "inducing_date": _iso(rec.inducing_date),
                    "path": rec.path,
                    "lines": [{"fix_parent_line": l.fix_parent_line,
                               "inducing_line": l.inducing_line,
                               "content": l.content} for l in rec.lines],
                    "evidence": list(rec.evidence),
                    "limitations": list(rec.limitations),
                }
                for rec in (trace.records if trace else [])
            ],
        })

    # 5. dataset + 6. model
    dataset_summary = {"status": "skipped", "reason": "disabled in analysis options"}
    model_summary = {"status": "unavailable", "reason": "model training was disabled in analysis options"}
    predictions = {
        "status": "unavailable",
        "reason": model_summary["reason"],
        "predictions": [],
        "horizon_days": options.horizon_days,
        "label_note": LABEL_DISCLAIMER,
        "limitations": PREDICTION_LIMITATIONS,
    }
    if options.train_model:
        stage("building_dataset")
        config = DatasetConfig(
            horizon_days=options.horizon_days,
            snapshot_interval_days=options.snapshot_interval_days,
            include_incomplete=True,  # the latest snapshot is what gets scored
            include_tests=not options.exclude_tests,
            source_extensions=extensions,
        )
        dataset = build_dataset(commits, config)
        dataset.metadata = {"repository": repo_name, "head_commit": head, **dataset.metadata}
        csv_path, _ = write_dataset(dataset, str(work_dir / "dataset.csv"), repo_path)
        meta = dataset.metadata
        dataset_summary = {
            "status": "built",
            "rows": meta["rows"],
            "labelled_rows": meta.get("labelled_rows", 0),
            "positives": meta["positives"],
            "snapshots_complete": len(meta["snapshots_complete"]),
            "snapshots_incomplete": len(meta["snapshots_incomplete"]),
        }

        stage("training_model")
        predictions.update(_model_results(str(csv_path), work_dir, risks))
        model_summary = {
            "status": predictions["status"],
            "reason": predictions.get("reason"),
            "scored_snapshot": predictions.get("scored_snapshot"),
        }
        if predictions.get("evaluation"):
            warnings.extend(predictions["evaluation"]["warnings"])
    stage("done")

    risk_counts = Counter(f["risk_level"] for f in files)
    summary = {
        "repository": {"id": repo_id, "name": repo_name, "head_commit": head},
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "observation_start": _iso(min((c.date for c in commits), default=None)),
        "observation_end": _iso(max((c.date for c in commits), default=None)),
        "commits_analyzed": len(commits),
        "files_analyzed": len(files),
        "files_touched_total": len(analysis.files),
        "risk_counts": {level: risk_counts.get(level, 0) for level in ("high", "medium", "low")},
        "risk_level_definitions": [
            {"code": code, "label": code.capitalize(), "rule": rule} for code, rule in LEVEL_DEFINITIONS
        ],
        "risk_factor_definitions": [
            {"code": d.code, "label": d.label, "rule": d.rule} for d in FACTOR_DEFINITIONS
        ],
        "bug_fix_counts": {o.value: outcome_counts.get(o.value, 0) for o in Outcome},
        "szz": szz,
        "dataset": dataset_summary,
        "model": model_summary,
        "timings_seconds": timings,
        "warnings": warnings,
    }
    return AnalysisResult(summary, files, bug_fixes, predictions)


def _model_results(csv_path: str, work_dir: Path, risks) -> dict:
    """Chronological evaluation plus a model refit on all labelled rows to score the latest snapshot."""
    out: dict = {}
    try:
        report = run_pipeline(csv_path, out_dir=str(work_dir / "model"))
        out["evaluation"] = _evaluation(report)
    except DatasetError as exc:
        out["evaluation"] = None
        out["evaluation_unavailable_reason"] = str(exc)

    if LogisticRegression is None:
        out.update(status="unavailable", reason="scikit-learn is not installed "
                   "(pip install -r requirements-ml.txt)")
        return out
    try:
        data = load_dataset(csv_path)
        latest = load_latest_snapshot(csv_path, data.features)
        if not latest:
            raise DatasetError("the dataset has no snapshot to score")
        probabilities, info = fit_and_score(data.rows, data.features, latest)
    except DatasetError as exc:
        out.update(status="unavailable", reason=f"model could not be trained: {exc}")
        return out

    order = sorted(range(len(latest)), key=lambda i: (-probabilities[i], latest[i].path))
    out.update(
        status="available",
        reason=None,
        scored_snapshot=latest[0].snapshot_date.isoformat(),
        trained_on_rows=info["trained_on_rows"],
        trained_on_positives=info["trained_on_positives"],
        predictions=[
            {
                "rank": rank,
                "path": latest[i].path,
                "probability": round(probabilities[i], 6),
                "heuristic_risk_level": risks[latest[i].path].level if latest[i].path in risks else None,
            }
            for rank, i in enumerate(order, start=1)
        ],
    )
    return out


def _evaluation(report: dict) -> dict:
    split = report["split"]
    models = []
    for name, m in report["models"].items():
        if m.get("status") == "skipped":
            models.append({"name": name, "status": "skipped", "reason": m["reason"]})
            continue
        models.append({
            "name": name,
            "status": "trained",
            "description": m.get("description"),
            "precision": m["precision"],
            "recall": m["recall"],
            "f1": m["f1"],
            "pr_auc": m["pr_auc"],
            "roc_auc": m["roc_auc"],
            "flagged": m["flagged"],
            "precision_at_k": {k: (v["precision"] if v else None) for k, v in m["at_k"].items()},
            "recall_at_k": {k: (v["recall"] if v else None) for k, v in m["at_k"].items()},
        })
    return {
        "method": split["method"],
        "test_start": split["test_start"],
        "train_rows": split["train"]["rows"],
        "train_positives": split["train"]["positives"],
        "test_rows": split["test"]["rows"],
        "test_positives": split["test"]["positives"],
        "test_prevalence": split["test"]["prevalence"],
        "purged_train_rows": split["purged_train_rows"],
        "models": models,
        "warnings": report["warnings"],
    }
