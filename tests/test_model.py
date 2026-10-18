import csv
import io
import json
import math
import os
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from unittest import mock

from reporisk import model
from reporisk.model import (
    DatasetError,
    Preprocessor,
    Row,
    average_precision,
    binary_metrics,
    chronological_split,
    churn_scores,
    evaluate_ranking,
    is_outcome_column,
    load_dataset,
    main,
    parse_number,
    roc_auc,
    run_pipeline,
    select_features,
)
from reporisk.schema import COLUMNS, FEATURE_COLUMNS, LABEL_COLUMNS

HAS_SKLEARN = model.LogisticRegression is not None
D0 = date(2020, 1, 1)


def make_rows(n_dates=8, n_files=12, interval=30, seed=0):
    """Synthetic dataset rows: high-churn files are more likely to be labelled 1."""
    rng = random.Random(seed)
    rows = []
    for d in range(n_dates):
        snap = D0 + timedelta(days=d * interval)
        for f in range(n_files):
            churn = (f + 1) * 10 + d
            label = 1 if rng.random() < (0.05 + 0.6 * f / n_files) else 0
            row = {c: 0 for c in FEATURE_COLUMNS}
            row.update(
                snapshot_date=snap.isoformat(), path=f"pkg/file{f}.py",
                commits=f + d, lines_added=churn // 2, lines_deleted=churn - churn // 2,
                churn=churn, churn_90d=f, authors=1 + f % 3, author_hhi=0.5,
                age_days=d * interval + 5, days_since_last_change=f % 7,
                label=label, window_complete=1, label_fix_count=label,
                label_fix_commits="abc" if label else "", deleted_in_window=0,
            )
            rows.append(row)
    return rows


def write_csv(directory, rows, name="ds.csv", horizon=60, metadata=True, columns=COLUMNS):
    path = os.path.join(directory, name)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    if metadata:
        meta = {"config": {"horizon_days": horizon}, "feature_columns": list(FEATURE_COLUMNS),
                "label_columns": list(LABEL_COLUMNS)}
        with open(path[:-4] + ".meta.json", "w") as f:
            json.dump(meta, f)
    return path


def row(day, label, churn=1.0, path="a.py"):
    features = tuple(churn if c == "churn" else 0.0 for c in FEATURE_COLUMNS)
    return Row(D0 + timedelta(days=day), path, label, features)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class FeatureSelectionTests(TempDirTestCase):
    def test_outcome_columns_are_never_features(self):
        features, excluded = select_features(COLUMNS)
        self.assertEqual(features, list(FEATURE_COLUMNS))
        for col in LABEL_COLUMNS + ("snapshot_date", "path"):
            self.assertNotIn(col, features)
            self.assertIn(col, excluded)

    def test_outcome_name_patterns(self):
        for name in ["label", "label_fix_count", "label_fix_commits", "deleted_in_window",
                     "window_complete", "future_bugs", "Label_v2", "path"]:
            self.assertTrue(is_outcome_column(name), name)
        for name in FEATURE_COLUMNS:
            self.assertFalse(is_outcome_column(name), name)

    def test_metadata_declaring_outcome_columns_is_rejected(self):
        with self.assertRaises(DatasetError):
            select_features(COLUMNS, {"feature_columns": ["churn", "label_fix_count"]})

    def test_unknown_columns_are_excluded(self):
        header = list(COLUMNS) + ["path_depth", "future_bugs"]
        features, excluded = select_features(header)
        self.assertNotIn("path_depth", features)
        self.assertIn("future_bugs", excluded)

    def test_metadata_narrows_features(self):
        features, _ = select_features(COLUMNS, {"feature_columns": ["churn", "commits"]})
        self.assertEqual(features, ["commits", "churn"])

    def test_no_features(self):
        with self.assertRaises(DatasetError):
            select_features(["snapshot_date", "path", "label", "label_fix_count"])

    def test_missing_feature_columns_are_tolerated(self):
        columns = ("snapshot_date", "path", "churn", "commits", "label")
        path = write_csv(self.dir, make_rows(), columns=columns, metadata=False)
        self.assertEqual(load_dataset(path).features, ["commits", "churn"])


class LoadingAndMissingValueTests(TempDirTestCase):
    def test_parse_number(self):
        self.assertEqual(parse_number("3"), 3.0)
        self.assertEqual(parse_number(" 2.5 "), 2.5)
        for bad in ["", "  ", "abc", "nan", "NaN", "inf", "-inf", None]:
            self.assertIsNone(parse_number(bad), bad)

    def test_blank_and_incomplete_labels_are_dropped(self):
        rows = make_rows(n_dates=3, n_files=2)
        rows[0]["label"] = ""
        rows[1]["window_complete"] = 0
        data = load_dataset(write_csv(self.dir, rows))
        self.assertEqual(data.dropped_unlabelled, 2)
        self.assertEqual(len(data.rows), 4)

    def test_invalid_label_and_date(self):
        rows = make_rows(n_dates=2, n_files=2)
        rows[0]["label"] = 2
        with self.assertRaises(DatasetError):
            load_dataset(write_csv(self.dir, rows, name="bad_label.csv"))
        rows = make_rows(n_dates=2, n_files=2)
        rows[0]["snapshot_date"] = "01/02/2020"
        with self.assertRaises(DatasetError):
            load_dataset(write_csv(self.dir, rows, name="bad_date.csv"))

    def test_missing_file_and_required_columns(self):
        with self.assertRaises(DatasetError):
            load_dataset(os.path.join(self.dir, "nope.csv"))
        path = write_csv(self.dir, make_rows(), columns=("path", "churn", "label"), metadata=False)
        with self.assertRaises(DatasetError):
            load_dataset(path)

    def test_bad_cells_become_missing(self):
        rows = make_rows(n_dates=2, n_files=2)
        rows[0]["churn"] = "n/a"
        rows[1]["commits"] = ""
        data = load_dataset(write_csv(self.dir, rows))
        churn = data.features.index("churn")
        self.assertIn(None, [r.features[churn] for r in data.rows])

    def test_imputation_uses_training_median(self):
        train = [row(0, 0, 1.0), row(0, 1, None), row(0, 0, 3.0)]
        prep = Preprocessor(FEATURE_COLUMNS).fit(train)
        j = FEATURE_COLUMNS.index("churn")
        self.assertEqual(prep.medians[j], 2.0)
        missing, two = prep.transform([row(9, 0, None), row(9, 0, 2.0)])
        self.assertEqual(missing[j], two[j])

    def test_all_missing_column(self):
        prep = Preprocessor(["churn"]).fit([Row(D0, "a", 0, (None,)), Row(D0, "b", 1, (None,))])
        self.assertEqual((prep.medians, prep.stds), ([0.0], [1.0]))
        self.assertEqual(prep.transform([Row(D0, "c", 0, (None,))]), [[0.0]])

    def test_transform_before_fit(self):
        with self.assertRaises(RuntimeError):
            Preprocessor(["churn"]).transform([row(0, 0)])


class ChronologicalSplitTests(unittest.TestCase):
    def rows(self, days=(0, 30, 60, 90), per_day=3):
        return [row(d, i % 2, path=f"f{i}.py") for d in days for i in range(per_day)]

    def test_test_set_is_latest_dates(self):
        split = chronological_split(self.rows(), test_fraction=0.25)
        self.assertEqual(split.test_start, D0 + timedelta(days=90))
        self.assertTrue(max(r.snapshot_date for r in split.train)
                        < min(r.snapshot_date for r in split.test))
        self.assertEqual(len(split.test), 3)

    def test_same_date_never_split(self):
        split = chronological_split(self.rows(per_day=5), test_fraction=0.5)
        train_dates = {r.snapshot_date for r in split.train}
        test_dates = {r.snapshot_date for r in split.test}
        self.assertFalse(train_dates & test_dates)

    def test_purge_drops_train_rows_whose_label_window_overlaps_test(self):
        split = chronological_split(self.rows(), test_fraction=0.25, purge_days=60)
        # test starts day 90; day 60 + 60 > 90 is purged, day 30 + 60 = 90 is kept
        self.assertEqual({(r.snapshot_date - D0).days for r in split.train}, {0, 30})
        self.assertEqual(split.purged, 3)

    def test_explicit_test_start(self):
        split = chronological_split(self.rows(), test_start=D0 + timedelta(days=60))
        self.assertEqual(len(split.test), 6)

    def test_errors(self):
        with self.assertRaises(DatasetError):
            chronological_split(self.rows(days=(0,)))  # one snapshot date
        with self.assertRaises(DatasetError):
            chronological_split(self.rows(), test_fraction=1.0)
        with self.assertRaises(DatasetError):
            chronological_split(self.rows(), purge_days=-1)
        with self.assertRaises(DatasetError):
            chronological_split(self.rows(days=(0, 30)), purge_days=365)  # purges all training

    def test_single_class_warning(self):
        rows = [row(0, 0), row(0, 1), row(30, 0), row(30, 0)]
        split = chronological_split(rows, test_fraction=0.5)
        self.assertTrue(any("single class" in w for w in split.warnings))

    def test_few_positives_warning(self):
        rows = [row(0, i < 3, path=f"f{i}") for i in range(20)] + [row(30, 1), row(30, 0)]
        split = chronological_split(rows, test_fraction=0.5)
        self.assertTrue(any("only 3 positives" in w for w in split.warnings))


class MetricTests(unittest.TestCase):
    def test_average_precision(self):
        self.assertAlmostEqual(average_precision([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]), 5 / 6)
        self.assertAlmostEqual(average_precision([1, 0], [0.5, 0.5]), 0.5)  # ties
        self.assertIsNone(average_precision([0, 0], [0.2, 0.1]))

    def test_roc_auc(self):
        self.assertAlmostEqual(roc_auc([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]), 0.75)
        self.assertAlmostEqual(roc_auc([1, 0], [0.5, 0.5]), 0.5)
        self.assertIsNone(roc_auc([1, 1], [0.5, 0.2]))

    def test_undefined_binary_metrics(self):
        nothing_flagged = binary_metrics([1, 0], [0, 0])
        self.assertIsNone(nothing_flagged["precision"])
        self.assertIsNone(nothing_flagged["f1"])
        no_positives = binary_metrics([0, 0], [1, 0])
        self.assertIsNone(no_positives["recall"])
        all_wrong = binary_metrics([1, 0], [0, 1])
        self.assertEqual((all_wrong["precision"], all_wrong["recall"], all_wrong["f1"]), (0.0, 0.0, 0.0))
        self.assertEqual(all_wrong["confusion_matrix"], {"tp": 0, "fp": 1, "fn": 1, "tn": 0})

    def test_ranking_metrics(self):
        y = [1, 0, 1, 0, 0]
        result = evaluate_ranking(y, [5, 4, 3, 2, 1], flag_count=2, top_k=[1, 3, 10])
        self.assertEqual(result["confusion_matrix"], {"tp": 1, "fp": 1, "fn": 1, "tn": 2})
        self.assertEqual(result["at_k"]["1"], {"precision": 1.0, "recall": 0.5})
        self.assertAlmostEqual(result["at_k"]["3"]["precision"], 2 / 3)
        self.assertIsNone(result["at_k"]["10"])  # more than the number of rows
        self.assertAlmostEqual(result["prevalence"], 0.4)

    def test_single_class_test_set(self):
        result = evaluate_ranking([0, 0, 0], [3, 2, 1], flag_count=1, top_k=[2])
        self.assertIsNone(result["pr_auc"])
        self.assertIsNone(result["roc_auc"])
        self.assertIsNone(result["recall"])
        self.assertEqual(result["precision"], 0.0)

    def test_churn_baseline_ranks_missing_last(self):
        rows = [row(0, 0, 5.0), row(0, 0, None), row(0, 1, 9.0)]
        scores = churn_scores(rows, FEATURE_COLUMNS)
        self.assertEqual(scores[:1] + scores[2:], [5.0, 9.0])
        self.assertEqual(scores[1], -math.inf)
        with self.assertRaises(DatasetError):
            churn_scores(rows, ["commits"])

    @unittest.skipUnless(HAS_SKLEARN, "scikit-learn is not installed")
    def test_metrics_match_scikit_learn(self):
        from sklearn.metrics import average_precision_score, roc_auc_score
        rng = random.Random(3)
        y = [int(rng.random() < 0.3) for _ in range(200)]
        scores = [round(rng.random(), 1) for _ in range(200)]  # rounding forces ties
        self.assertAlmostEqual(average_precision(y, scores), average_precision_score(y, scores))
        self.assertAlmostEqual(roc_auc(y, scores), roc_auc_score(y, scores))


class PipelineTests(TempDirTestCase):
    def run_quiet(self, path, **kwargs):
        kwargs.setdefault("out_dir", os.path.join(self.dir, "out"))
        return run_pipeline(path, **kwargs)

    def read_predictions(self, out="out"):
        with open(os.path.join(self.dir, out, "predictions.csv"), newline="") as f:
            return list(csv.DictReader(f))

    def test_outputs_and_same_test_rows_for_both_models(self):
        path = write_csv(self.dir, make_rows())
        report = self.run_quiet(path, out_dir=os.path.join(self.dir, "nested", "out"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "nested", "out", "metrics.json")))
        with open(os.path.join(self.dir, "nested", "out", "predictions.csv"), newline="") as f:
            predictions = list(csv.DictReader(f))
        self.assertEqual(len(predictions), report["split"]["test"]["rows"])
        baseline = report["models"]["churn_baseline"]
        self.assertEqual(baseline["rows"], report["split"]["test"]["rows"])
        self.assertEqual(report["split"]["purge_days"], 60)  # from metadata horizon
        lr = report["models"]["logistic_regression"]
        if HAS_SKLEARN:
            self.assertEqual(lr["status"], "trained")
            self.assertEqual(lr["rows"], baseline["rows"])
            self.assertEqual(lr["flagged"], baseline["flagged"])
            self.assertTrue(all(p["logreg_probability"] for p in predictions))
            self.assertEqual(set(lr["coefficients"]), set(report["features"]))
        else:
            self.assertEqual(lr, {"status": "skipped", "reason": "scikit-learn is not installed"})
            self.assertTrue(all(p["logreg_probability"] == "" for p in predictions))

    def test_flag_count_follows_training_prevalence(self):
        report = self.run_quiet(write_csv(self.dir, make_rows()))
        train = report["split"]["train"]
        expected = max(1, round(train["positives"] / train["rows"] * report["split"]["test"]["rows"]))
        self.assertEqual(report["flagged_per_model"], expected)

    def test_logreg_skipped_without_scikit_learn(self):
        with mock.patch.object(model, "LogisticRegression", None):
            report = self.run_quiet(write_csv(self.dir, make_rows()))
        self.assertEqual(report["models"]["logistic_regression"]["reason"],
                         "scikit-learn is not installed")
        self.assertIn("pr_auc", report["models"]["churn_baseline"])

    def test_outcome_columns_cannot_influence_predictions(self):
        rows = make_rows()
        first = self.run_quiet(write_csv(self.dir, rows, name="a.csv"), out_dir=os.path.join(self.dir, "a"))
        for r in rows:  # scramble every outcome-derived column except the label itself
            r["label_fix_count"] = 999 if r["label"] else -1
            r["label_fix_commits"] = "leak" * r["label"]
            r["deleted_in_window"] = r["label"]
        second = self.run_quiet(write_csv(self.dir, rows, name="b.csv"), out_dir=os.path.join(self.dir, "b"))
        self.assertEqual(self.read_predictions("a"), self.read_predictions("b"))
        self.assertEqual(first["models"], second["models"])

    @unittest.skipUnless(HAS_SKLEARN, "scikit-learn is not installed")
    def test_test_rows_do_not_affect_training(self):
        rows = make_rows()
        first = self.run_quiet(write_csv(self.dir, rows, name="a.csv"), out_dir=os.path.join(self.dir, "a"))
        test_start = first["split"]["test_start"]
        for r in rows:
            if r["snapshot_date"] >= test_start:
                r["churn"] = 10 ** 6  # extreme test-period values
                r["commits"] = ""
        second = self.run_quiet(write_csv(self.dir, rows, name="b.csv"), out_dir=os.path.join(self.dir, "b"))
        lr1, lr2 = first["models"]["logistic_regression"], second["models"]["logistic_regression"]
        self.assertEqual(lr1["coefficients"], lr2["coefficients"])
        self.assertEqual(lr1["intercept"], lr2["intercept"])

    def test_missing_metadata_requires_purge_days(self):
        path = write_csv(self.dir, make_rows(), metadata=False)
        with self.assertRaises(DatasetError):
            self.run_quiet(path)
        report = self.run_quiet(path, purge_days=30)
        self.assertEqual(report["split"]["purge_days"], 30)

    def test_empty_dataset(self):
        with self.assertRaises(DatasetError):
            self.run_quiet(write_csv(self.dir, []))

    def test_cli(self):
        path = write_csv(self.dir, make_rows())
        out = os.path.join(self.dir, "cli")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main([path, "--out-dir", out, "--top-k", "5", "10", "--no-logreg"])
        self.assertEqual(code, 0)
        self.assertIn("churn_baseline", stdout.getvalue())
        self.assertIn("not confirmed defects", stdout.getvalue())
        with open(os.path.join(out, "metrics.json")) as f:
            metrics = json.load(f)
        self.assertEqual(set(metrics["models"]["churn_baseline"]["at_k"]), {"5", "10"})

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(main([os.path.join(self.dir, "missing.csv")]), 2)
        self.assertIn("dataset not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
