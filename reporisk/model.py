"""Train and evaluate future bug-fix prediction on a RepoRisk dataset.

Pipeline:
1. Load the CSV written by `--build-dataset` and validate its schema.
2. Select feature columns from an allowlist; outcome-derived columns can
   never become inputs.
3. Split chronologically by snapshot date, purging training rows whose label
   window reaches into the test period.
4. Fit preprocessing (median imputation, log1p, standardization) on the
   training rows only.
5. Score the same held-out rows with a churn-ranking baseline and, when
   scikit-learn is installed, a logistic regression.
6. Write metrics and per-row predictions.

Only the standard library is required. Labels are heuristic bug-fix
classifications, not confirmed defects, so metrics measure how well a model
predicts "a commit that looks like a bug fix will touch this file".

Run: python -m reporisk.model output/dataset.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from .schema import FEATURE_COLUMNS, ID_COLUMNS, LABEL_COLUMNS

try:  # optional dependency
    import sklearn
    from sklearn.linear_model import LogisticRegression
except ImportError:  # pragma: no cover - depends on the environment
    sklearn = None
    LogisticRegression = None

REQUIRED_COLUMNS = ("snapshot_date", "path", "label")
# Any column whose name contains one of these is treated as outcome-derived.
FORBIDDEN_SUBSTRINGS = ("label", "future", "window", "deleted_in")
# Bounded features; the rest are non-negative counts or day spans that get log1p.
UNBOUNDED_EXEMPT = ("author_hhi", "strongest_coupling")
BASELINE_FEATURE = "churn"
DEFAULT_TOP_K = (10, 20, 50)
DEFAULT_TEST_FRACTION = 0.25
DEFAULT_OUT_DIR = "output/model"
MIN_POSITIVES_WARNING = 10

LABEL_DISCLAIMER = (
    "Labels mark files touched by commits that a heuristic classifies as likely bug "
    "fixes. They are not confirmed defects; metrics measure agreement with that heuristic."
)


class DatasetError(ValueError):
    """The dataset can't be used for training as given."""


@dataclass(frozen=True)
class Row:
    snapshot_date: date
    path: str
    label: int
    features: tuple[float | None, ...]  # in LoadedData.features order


@dataclass
class LoadedData:
    rows: list[Row]
    features: list[str]
    excluded_columns: list[str]
    dropped_unlabelled: int
    horizon_days: int | None
    metadata_found: bool


@dataclass
class Split:
    train: list[Row]
    test: list[Row]
    test_start: date
    purged: int
    warnings: list[str] = field(default_factory=list)


def is_outcome_column(name: str) -> bool:
    """True for columns that must never be model inputs."""
    return (
        name in LABEL_COLUMNS
        or name in ID_COLUMNS
        or any(part in name.lower() for part in FORBIDDEN_SUBSTRINGS)
    )


def select_features(header: Sequence[str], metadata: dict | None = None) -> tuple[list[str], list[str]]:
    """Return (features, excluded columns).

    Features are FEATURE_COLUMNS present in the CSV, narrowed to the metadata's
    `feature_columns` when given. Metadata that declares an outcome-derived
    column as a feature is rejected outright.
    """
    candidates = [c for c in FEATURE_COLUMNS if c in header]
    if metadata and metadata.get("feature_columns"):
        declared = list(metadata["feature_columns"])
        bad = [c for c in declared if is_outcome_column(c)]
        if bad:
            raise DatasetError(f"metadata lists outcome-derived columns as features: {bad}")
        candidates = [c for c in candidates if c in declared]
    features = [c for c in candidates if not is_outcome_column(c)]
    if not features:
        raise DatasetError("no usable feature columns found")
    excluded = [c for c in header if c not in features]
    return features, excluded


def parse_number(value: str | None) -> float | None:
    """Parse a CSV cell as a finite float; blanks, text, NaN and inf become None."""
    if value is None:
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _metadata_path(csv_path: Path) -> Path:
    return csv_path.with_name(csv_path.stem + ".meta.json")


def load_dataset(csv_path: str, metadata_path: str | None = None) -> LoadedData:
    """Load and validate a dataset CSV and, if present, its metadata JSON."""
    path = Path(csv_path)
    if not path.is_file():
        raise DatasetError(f"dataset not found: {path}")
    meta_file = Path(metadata_path) if metadata_path else _metadata_path(path)
    metadata = None
    if meta_file.is_file():
        with open(meta_file) as f:
            metadata = json.load(f)
    elif metadata_path:
        raise DatasetError(f"metadata not found: {meta_file}")

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise DatasetError(f"dataset is missing required columns: {missing}")
        features, excluded = select_features(header, metadata)

        rows, dropped = [], 0
        for line, record in enumerate(reader, start=2):
            label = (record.get("label") or "").strip()
            if label == "" or record.get("window_complete", "1").strip() == "0":
                dropped += 1  # incomplete future window: no trustworthy label
                continue
            if label not in ("0", "1"):
                raise DatasetError(f"line {line}: label must be 0, 1 or blank, got {label!r}")
            try:
                snapshot = date.fromisoformat(record["snapshot_date"].strip())
            except (ValueError, AttributeError):
                raise DatasetError(f"line {line}: invalid snapshot_date {record['snapshot_date']!r}")
            rows.append(Row(
                snapshot,
                record["path"],
                int(label),
                tuple(parse_number(record.get(c)) for c in features),
            ))

    horizon = None
    if metadata:
        horizon = (metadata.get("config") or {}).get("horizon_days")
    rows.sort(key=lambda r: (r.snapshot_date, r.path))
    return LoadedData(rows, features, excluded, dropped, horizon, metadata is not None)


def chronological_split(
    rows: Sequence[Row],
    *,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    test_start: date | None = None,
    purge_days: int = 0,
) -> Split:
    """Split by snapshot date: the latest dates are the test set.

    Training rows whose label window (T, T + purge_days] ends after the first
    test snapshot are dropped, because their labels use commits from the
    test period.
    """
    if not 0 < test_fraction < 1:
        raise DatasetError("test_fraction must be between 0 and 1")
    if purge_days < 0:
        raise DatasetError("purge_days must not be negative")
    dates = sorted({r.snapshot_date for r in rows})
    if len(dates) < 2:
        raise DatasetError(
            f"need at least two snapshot dates for a chronological split, found {len(dates)}"
        )
    if test_start is None:
        n_test = min(max(1, math.ceil(len(dates) * test_fraction)), len(dates) - 1)
        test_start = dates[-n_test]

    gap = timedelta(days=purge_days)
    train, test, purged = [], [], 0
    for r in rows:
        if r.snapshot_date >= test_start:
            test.append(r)
        elif r.snapshot_date + gap <= test_start:
            train.append(r)
        else:
            purged += 1
    if not train:
        raise DatasetError("no training rows before the test period (after purging)")
    if not test:
        raise DatasetError("no test rows on or after the test start date")

    split = Split(train, test, test_start, purged)
    for name, part in (("train", train), ("test", test)):
        positives = sum(r.label for r in part)
        if positives == 0 or positives == len(part):
            split.warnings.append(f"{name} set has a single class ({positives} positives of {len(part)})")
        elif positives < MIN_POSITIVES_WARNING:
            split.warnings.append(f"{name} set has only {positives} positives; metrics are unstable")
    return split


class Preprocessor:
    """Median imputation, log1p for count features, then standardization.

    All statistics come from the rows passed to `fit` (the training set).
    """

    def __init__(self, features: Sequence[str]):
        self.features = list(features)
        self.log = [f not in UNBOUNDED_EXEMPT for f in self.features]
        self.medians: list[float] = []
        self.means: list[float] = []
        self.stds: list[float] = []

    def _transform_value(self, j: int, value: float) -> float:
        return math.log1p(max(value, 0.0)) if self.log[j] else value

    def fit(self, rows: Sequence[Row]) -> "Preprocessor":
        self.medians, self.means, self.stds = [], [], []
        for j in range(len(self.features)):
            observed = sorted(r.features[j] for r in rows if r.features[j] is not None)
            self.medians.append(_median(observed) if observed else 0.0)
            values = [self._transform_value(j, self._impute(j, r.features[j])) for r in rows]
            mean = sum(values) / len(values)
            std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
            self.means.append(mean)
            self.stds.append(std if std > 0 else 1.0)
        return self

    def _impute(self, j: int, value: float | None) -> float:
        return self.medians[j] if value is None else value

    def transform(self, rows: Sequence[Row]) -> list[list[float]]:
        if not self.medians:
            raise RuntimeError("Preprocessor.fit must be called first")
        return [
            [
                (self._transform_value(j, self._impute(j, r.features[j])) - self.means[j]) / self.stds[j]
                for j in range(len(self.features))
            ]
            for r in rows
        ]


def _median(sorted_values: Sequence[float]) -> float:
    n = len(sorted_values)
    mid = n // 2
    return sorted_values[mid] if n % 2 else (sorted_values[mid - 1] + sorted_values[mid]) / 2


def churn_scores(rows: Sequence[Row], features: Sequence[str]) -> list[float]:
    """Baseline: rank files by historical churn. Missing churn ranks last."""
    if BASELINE_FEATURE not in features:
        raise DatasetError(f"baseline needs the {BASELINE_FEATURE!r} column")
    j = list(features).index(BASELINE_FEATURE)
    return [r.features[j] if r.features[j] is not None else -math.inf for r in rows]


# ---------------------------------------------------------------- metrics

def _safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def _ranking(scores: Sequence[float]) -> list[int]:
    """Row indices from highest to lowest score; ties keep row order (date, path)."""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def binary_metrics(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred) if not t and p)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t and not p)
    tn = sum(1 for t, p in zip(y_true, y_pred) if not t and not p)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = _safe_div(2 * precision * recall, precision + recall) or 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def average_precision(y_true: Sequence[int], scores: Sequence[float]) -> float | None:
    """Area under the precision-recall curve (step-wise, as sklearn's average_precision_score)."""
    positives = sum(y_true)
    if positives == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ap, tp, seen, prev_recall, i = 0.0, 0, 0, 0.0, 0
    while i < len(order):
        threshold = scores[order[i]]
        while i < len(order) and scores[order[i]] == threshold:  # tied scores share a threshold
            tp += y_true[order[i]]
            seen += 1
            i += 1
        recall = tp / positives
        ap += (recall - prev_recall) * (tp / seen)
        prev_recall = recall
    return ap


def roc_auc(y_true: Sequence[int], scores: Sequence[float]) -> float | None:
    """Probability a random positive outranks a random negative (ties count half)."""
    pos = [s for s, t in zip(scores, y_true) if t]
    neg = [s for s, t in zip(scores, y_true) if not t]
    if not pos or not neg:
        return None
    # rank-sum (Mann-Whitney U) with average ranks for ties
    ordered = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and scores[ordered[j + 1]] == scores[ordered[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[ordered[k]] = (i + j) / 2 + 1
        i = j + 1
    pos_rank_sum = sum(r for r, t in zip(ranks, y_true) if t)
    return (pos_rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def evaluate_ranking(
    y_true: Sequence[int], scores: Sequence[float], *, flag_count: int, top_k: Sequence[int]
) -> dict:
    """Metrics for a scorer whose top `flag_count` rows are predicted positive."""
    n, positives = len(y_true), sum(y_true)
    order = _ranking(scores)
    flagged = set(order[:flag_count])
    result = {
        "rows": n,
        "positives": positives,
        "prevalence": _safe_div(positives, n),
        "flagged": flag_count,
        **binary_metrics(y_true, [1 if i in flagged else 0 for i in range(n)]),
        "pr_auc": average_precision(y_true, scores),
        "roc_auc": roc_auc(y_true, scores),
        "at_k": {},
    }
    for k in top_k:
        if k > n:
            result["at_k"][str(k)] = None  # fewer rows than k
            continue
        hits = sum(y_true[i] for i in order[:k])
        result["at_k"][str(k)] = {"precision": hits / k, "recall": _safe_div(hits, positives)}
    return result


# ---------------------------------------------------------------- training

def train_logistic_regression(train_x: list[list[float]], train_y: list[int]):
    """Fit a class-balanced logistic regression; returns (model, reason_if_skipped)."""
    if LogisticRegression is None:
        return None, "scikit-learn is not installed"
    if len(set(train_y)) < 2:
        return None, "training set has a single class"
    model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=0)
    model.fit(train_x, train_y)
    return model, None


def run_pipeline(
    csv_path: str,
    *,
    out_dir: str = DEFAULT_OUT_DIR,
    metadata_path: str | None = None,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    test_start: date | None = None,
    purge_days: int | None = None,
    top_k: Sequence[int] = DEFAULT_TOP_K,
    use_logreg: bool = True,
) -> dict:
    """Run the whole pipeline and write metrics.json and predictions.csv."""
    data = load_dataset(csv_path, metadata_path)
    if not data.rows:
        raise DatasetError("dataset has no labelled rows")
    if purge_days is None:
        if data.horizon_days is None:
            raise DatasetError(
                "no metadata with horizon_days found; pass --purge-days (the dataset's horizon)"
            )
        purge_days = int(data.horizon_days)

    split = chronological_split(
        data.rows, test_fraction=test_fraction, test_start=test_start, purge_days=purge_days
    )
    train_y = [r.label for r in split.train]
    test_y = [r.label for r in split.test]
    train_prevalence = sum(train_y) / len(train_y)
    # Flag as many test rows as the training prevalence predicts, so both
    # scorers are compared at the same alert budget.
    flag_count = max(1, round(train_prevalence * len(split.test)))

    baseline = churn_scores(split.test, data.features)
    models = {
        "churn_baseline": {
            "description": f"rank by historical {BASELINE_FEATURE}; top {flag_count} flagged",
            **evaluate_ranking(test_y, baseline, flag_count=flag_count, top_k=top_k),
        }
    }

    probabilities = None
    if use_logreg:
        prep = Preprocessor(data.features).fit(split.train)
        model, skipped = train_logistic_regression(prep.transform(split.train), train_y)
        if model is None:
            models["logistic_regression"] = {"status": "skipped", "reason": skipped}
        else:
            probabilities = [float(p[1]) for p in model.predict_proba(prep.transform(split.test))]
            models["logistic_regression"] = {
                "status": "trained",
                "description": "class-balanced L2 logistic regression on standardized "
                               f"log1p features; top {flag_count} flagged",
                **evaluate_ranking(test_y, probabilities, flag_count=flag_count, top_k=top_k),
                "at_probability_0_5": binary_metrics(test_y, [int(p >= 0.5) for p in probabilities]),
                "coefficients": dict(zip(data.features, (round(float(c), 6) for c in model.coef_[0]))),
                "intercept": round(float(model.intercept_[0]), 6),
            }
    else:
        models["logistic_regression"] = {"status": "skipped", "reason": "disabled with --no-logreg"}

    report = {
        "dataset": str(csv_path),
        "metadata_found": data.metadata_found,
        "label_note": LABEL_DISCLAIMER,
        "features": data.features,
        "excluded_columns": data.excluded_columns,
        "preprocessing": "median imputation, log1p on count features, standardization; "
                         "all fitted on training rows only",
        "scikit_learn_version": getattr(sklearn, "__version__", None),
        "split": {
            "method": "chronological by snapshot_date",
            "test_start": split.test_start.isoformat(),
            "purge_days": purge_days,
            "purged_train_rows": split.purged,
            "dropped_unlabelled_rows": data.dropped_unlabelled,
            "train": _part_summary(split.train),
            "test": _part_summary(split.test),
        },
        "flagged_per_model": flag_count,
        "models": models,
        "warnings": split.warnings,
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "metrics.json", "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    _write_predictions(out / "predictions.csv", split.test, baseline, probabilities)
    report["outputs"] = [str(out / "metrics.json"), str(out / "predictions.csv")]
    return report


@dataclass(frozen=True)
class SnapshotRow:
    """Features of one file at one snapshot, with no label attached."""

    snapshot_date: date
    path: str
    features: tuple[float | None, ...]


def load_latest_snapshot(csv_path: str, features: Sequence[str]) -> list[SnapshotRow]:
    """Feature rows of the most recent snapshot in the CSV, labelled or not.

    The latest snapshot usually has an incomplete future window, which is
    exactly the situation a forecast is for. Label columns are not read.
    """
    with open(csv_path, newline="") as f:
        records = list(csv.DictReader(f))
    dated = []
    for record in records:
        try:
            dated.append((date.fromisoformat(record["snapshot_date"].strip()), record))
        except (ValueError, KeyError, AttributeError):
            continue
    if not dated:
        return []
    latest = max(d for d, _ in dated)
    return sorted(
        (SnapshotRow(d, r["path"], tuple(parse_number(r.get(c)) for c in features))
         for d, r in dated if d == latest),
        key=lambda r: r.path,
    )


def fit_and_score(
    labelled: Sequence[Row], features: Sequence[str], to_score: Sequence[SnapshotRow]
) -> tuple[list[float], dict]:
    """Fit preprocessing and logistic regression on all labelled rows, then score `to_score`.

    Raises DatasetError when the model can't be trained. This model has no
    held-out evaluation of its own; use run_pipeline's chronological metrics.
    """
    if not labelled:
        raise DatasetError("no labelled rows to train on")
    prep = Preprocessor(features).fit(labelled)
    model, skipped = train_logistic_regression(prep.transform(labelled), [r.label for r in labelled])
    if model is None:
        raise DatasetError(skipped)
    probabilities = [float(p[1]) for p in model.predict_proba(prep.transform(to_score))] if to_score else []
    info = {
        "trained_on_rows": len(labelled),
        "trained_on_positives": sum(r.label for r in labelled),
        "coefficients": dict(zip(features, (round(float(c), 6) for c in model.coef_[0]))),
        "intercept": round(float(model.intercept_[0]), 6),
    }
    return probabilities, info


def _part_summary(rows: Sequence[Row]) -> dict:
    dates = sorted({r.snapshot_date for r in rows})
    positives = sum(r.label for r in rows)
    return {
        "rows": len(rows),
        "positives": positives,
        "prevalence": _safe_div(positives, len(rows)),
        "snapshots": len(dates),
        "first_snapshot": dates[0].isoformat(),
        "last_snapshot": dates[-1].isoformat(),
    }


def _write_predictions(path: Path, rows, baseline, probabilities) -> None:
    churn_rank = {i: n for n, i in enumerate(_ranking(baseline), start=1)}
    lr_rank = {i: n for n, i in enumerate(_ranking(probabilities), start=1)} if probabilities else {}
    with open(path, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["snapshot_date", "path", "label", "churn_score", "churn_rank",
                         "logreg_probability", "logreg_rank"])
        for i, r in enumerate(rows):
            writer.writerow([
                r.snapshot_date.isoformat(), r.path, r.label,
                "" if baseline[i] == -math.inf else baseline[i], churn_rank[i],
                "" if probabilities is None else round(probabilities[i], 6),
                lr_rank.get(i, ""),
            ])


# ---------------------------------------------------------------- CLI

def _fmt(value) -> str:
    return "undefined" if value is None else f"{value:.3f}"


def _print_report(report: dict) -> None:
    split = report["split"]
    train, test = split["train"], split["test"]
    print(f"Features ({len(report['features'])}): {', '.join(report['features'])}")
    print(f"Train: {train['rows']} rows, {train['positives']} positives, "
          f"{train['first_snapshot']} .. {train['last_snapshot']}")
    print(f"Test:  {test['rows']} rows, {test['positives']} positives "
          f"(prevalence {_fmt(test['prevalence'])}), {test['first_snapshot']} .. {test['last_snapshot']}")
    print(f"Purged {split['purged_train_rows']} train rows within {split['purge_days']} days of the "
          f"test start; each model flags its top {report['flagged_per_model']} test rows\n")

    header = f"{'model':<22}{'precision':>10}{'recall':>8}{'f1':>8}{'pr_auc':>8}{'roc_auc':>9}"
    ks = list(report["models"]["churn_baseline"]["at_k"])
    header += "".join(f"{'P@' + k:>8}" for k in ks)
    print(header)
    for name, m in report["models"].items():
        if m.get("status") == "skipped":
            print(f"{name:<22}skipped: {m['reason']}")
            continue
        line = (f"{name:<22}{_fmt(m['precision']):>10}{_fmt(m['recall']):>8}{_fmt(m['f1']):>8}"
                f"{_fmt(m['pr_auc']):>8}{_fmt(m['roc_auc']):>9}")
        line += "".join(f"{_fmt(m['at_k'][k]['precision'] if m['at_k'][k] else None):>8}" for k in ks)
        print(line)
    for w in report["warnings"]:
        print(f"warning: {w}")
    print(f"\n{report['label_note']}")
    print("Wrote " + "\n      ".join(report["outputs"]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m reporisk.model",
        description="Train and evaluate future bug-fix prediction on a RepoRisk dataset CSV.",
    )
    parser.add_argument("dataset", help="CSV written by `python -m reporisk REPO --build-dataset`")
    parser.add_argument("--metadata", help="metadata JSON (default: <dataset>.meta.json beside the CSV)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help=f"where to write metrics.json and predictions.csv (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--test-fraction", type=float, default=DEFAULT_TEST_FRACTION,
                        help="share of the latest snapshot dates used for testing (default: 0.25)")
    parser.add_argument("--test-start", type=date.fromisoformat, metavar="DATE",
                        help="first test snapshot date (overrides --test-fraction)")
    parser.add_argument("--purge-days", type=int,
                        help="drop training rows this close to the test start (default: the "
                             "dataset's horizon from its metadata)")
    parser.add_argument("--top-k", type=int, nargs="+", default=list(DEFAULT_TOP_K), metavar="K",
                        help="K values for precision/recall@K (default: 10 20 50)")
    parser.add_argument("--no-logreg", action="store_true", help="only run the churn baseline")
    args = parser.parse_args(argv)
    try:
        report = run_pipeline(
            args.dataset,
            out_dir=args.out_dir,
            metadata_path=args.metadata,
            test_fraction=args.test_fraction,
            test_start=args.test_start,
            purge_days=args.purge_days,
            top_k=args.top_k,
            use_logreg=not args.no_logreg,
        )
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
