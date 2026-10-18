import csv
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from reporisk.bug_fixes import Outcome
from reporisk.dataset import (
    COLUMNS,
    FEATURE_COLUMNS,
    ID_COLUMNS,
    LABEL_COLUMNS,
    DatasetConfig,
    alive_files,
    build_dataset,
    build_dataset_for_repo,
    end_of_day,
    snapshot_schedule,
    write_dataset,
)
from reporisk.mining import CommitRecord, FileChange, read_commits
from tests.test_szz import TempRepo

UTC = timezone.utc
START = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
SNAP = date(2024, 2, 1)  # snapshot used by most tests
CUTOFF = end_of_day(SNAP)


def change(path, added=1, deleted=0, kind="MODIFY", old=None):
    return FileChange(path, added, deleted, old or path, kind)


def commit(sha, when, message, *changes, author="dev@x.com"):
    return CommitRecord(sha, author, when, tuple(changes), message)


def config(**overrides):
    defaults = dict(horizon_days=30, snapshot_interval_days=31, first_snapshot=SNAP)
    defaults.update(overrides)
    return DatasetConfig(**defaults)


def history(*future, end=None):
    """Two files created before the snapshot, the given future commits, and a
    final commit far enough out that the snapshot's window is fully observed."""
    base = [
        commit("c1", START, "Add app", change("app.py", 10, kind="ADD"),
               change("util.py", 5, kind="ADD")),
        commit("c2", START + timedelta(days=10), "Tune app", change("app.py", 2, 1)),
    ]
    tail = [commit("zz", end or CUTOFF + timedelta(days=60), "Add later", change("later.py", 1, kind="ADD"))]
    return base + list(future) + tail


def rows_by_path(dataset, snapshot=SNAP.isoformat()):
    return {r["path"]: r for r in dataset.rows if r["snapshot_date"] == snapshot}


class LabelTests(unittest.TestCase):
    def test_positive_and_negative_labels(self):
        fix = commit("fix1", CUTOFF + timedelta(days=5), "Fix crash in app", change("app.py", 3, 2))
        rows = rows_by_path(build_dataset(history(fix), config()))
        self.assertEqual(set(rows), {"app.py", "util.py"})
        self.assertEqual(rows["app.py"]["label"], 1)
        self.assertEqual(rows["app.py"]["label_fix_count"], 1)
        self.assertEqual(rows["app.py"]["label_fix_commits"], "fix1")
        self.assertEqual(rows["util.py"]["label"], 0)
        self.assertEqual(rows["util.py"]["label_fix_count"], 0)

    def test_non_bug_fix_commits_do_not_label(self):
        typo = commit("t1", CUTOFF + timedelta(days=5), "Fix typo in app", change("app.py", 1, 1))
        feature = commit("f1", CUTOFF + timedelta(days=6), "Add retries", change("util.py", 9))
        rows = rows_by_path(build_dataset(history(typo, feature), config()))
        self.assertEqual((rows["app.py"]["label"], rows["util.py"]["label"]), (0, 0))

    def test_uncertain_fixes_only_count_when_configured(self):
        uncertain = commit("u1", CUTOFF + timedelta(days=5), "Fix proxy handling", change("app.py"))
        default = rows_by_path(build_dataset(history(uncertain), config()))
        self.assertEqual(default["app.py"]["label"], 0)
        widened = config(label_outcomes=(Outcome.LIKELY, Outcome.UNCERTAIN))
        self.assertEqual(rows_by_path(build_dataset(history(uncertain), widened))["app.py"]["label"], 1)

    def test_multi_file_fix_labels_every_touched_sample(self):
        fix = commit("fix1", CUTOFF + timedelta(days=5), "Fix regression",
                     change("app.py"), change("util.py"))
        rows = rows_by_path(build_dataset(history(fix), config()))
        self.assertEqual((rows["app.py"]["label"], rows["util.py"]["label"]), (1, 1))


class WindowBoundaryTests(unittest.TestCase):
    def label_for(self, when):
        fix = commit("fix1", when, "Fix crash", change("app.py"))
        return rows_by_path(build_dataset(history(fix), config()))["app.py"]

    def test_fix_at_cutoff_is_past_not_future(self):
        row = self.label_for(CUTOFF)
        self.assertEqual(row["label"], 0)
        self.assertEqual(row["past_bug_fixes"], 1)

    def test_fix_just_after_cutoff(self):
        self.assertEqual(self.label_for(CUTOFF + timedelta(microseconds=1))["label"], 1)

    def test_fix_at_window_end_is_included(self):
        self.assertEqual(self.label_for(CUTOFF + timedelta(days=30))["label"], 1)

    def test_fix_after_window_end_is_excluded(self):
        row = self.label_for(CUTOFF + timedelta(days=30, microseconds=1))
        self.assertEqual(row["label"], 0)
        self.assertEqual(row["past_bug_fixes"], 0)

    def test_naive_dates_are_utc(self):
        naive = commit("fix1", (CUTOFF + timedelta(days=1)).replace(tzinfo=None), "Fix crash",
                       change("app.py"))
        self.assertEqual(rows_by_path(build_dataset(history(naive), config()))["app.py"]["label"], 1)


class IncompleteWindowTests(unittest.TestCase):
    def test_incomplete_snapshot_dropped_by_default(self):
        # last commit 10 days after the snapshot: a 30-day window can't be observed
        commits = history(end=CUTOFF + timedelta(days=10))
        dataset = build_dataset(commits, config())
        self.assertEqual(dataset.rows, [])
        self.assertEqual(dataset.metadata["snapshots_incomplete"], [SNAP.isoformat()])
        self.assertEqual(dataset.metadata["snapshots_complete"], [])

    def test_incomplete_rows_get_blank_labels_never_zero(self):
        fix = commit("fix1", CUTOFF + timedelta(days=2), "Fix crash", change("app.py"))
        commits = history(fix, end=CUTOFF + timedelta(days=10))
        rows = rows_by_path(build_dataset(commits, config(include_incomplete=True)))
        for row in rows.values():
            self.assertEqual(row["window_complete"], 0)
            self.assertIsNone(row["label"])
            self.assertIsNone(row["label_fix_count"])

    def test_explicit_observation_end_ignores_later_commits(self):
        fix = commit("fix1", CUTOFF + timedelta(days=5), "Fix crash", change("app.py"))
        cfg = config(observation_end=CUTOFF + timedelta(days=20))
        dataset = build_dataset(history(fix), cfg)
        self.assertEqual(dataset.rows, [])  # window ends after observation_end
        self.assertEqual(dataset.metadata["commits_used"], 3)

    def test_schedule(self):
        cfg = DatasetConfig(horizon_days=10, snapshot_interval_days=10)
        complete, incomplete = snapshot_schedule(START, START + timedelta(days=45), cfg)
        self.assertEqual(complete, [date(2024, 1, 11), date(2024, 1, 21), date(2024, 1, 31)])
        self.assertEqual(incomplete, [date(2024, 2, 10)])

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            DatasetConfig(horizon_days=0)
        with self.assertRaises(ValueError):
            DatasetConfig(snapshot_interval_days=-5)
        with self.assertRaises(ValueError):
            DatasetConfig(label_outcomes=())


class LeakageTests(unittest.TestCase):
    def test_future_commits_never_change_features(self):
        quiet = build_dataset(history(), config())
        noisy_future = [
            commit("big", CUTOFF + timedelta(days=1), "Rewrite app", change("app.py", 5000, 4000),
                   author="new@x.com"),
            commit("fix1", CUTOFF + timedelta(days=2), "Fix crash", change("app.py", 1, 1),
                   change("util.py", 1, 1)),
            commit("del", CUTOFF + timedelta(days=3), "Remove util", change("util.py", 0, 5, "DELETE")),
        ]
        noisy = build_dataset(history(*noisy_future), config())
        for path in ("app.py", "util.py"):
            before, after = rows_by_path(quiet)[path], rows_by_path(noisy)[path]
            self.assertEqual({k: before[k] for k in FEATURE_COLUMNS},
                             {k: after[k] for k in FEATURE_COLUMNS}, path)
        self.assertEqual(rows_by_path(noisy)["app.py"]["label"], 1)

    def test_past_bug_fixes_only_counts_past(self):
        past_fix = commit("pf", CUTOFF - timedelta(days=3), "Fix crash", change("app.py"))
        future_fix = commit("ff", CUTOFF + timedelta(days=3), "Fix bug", change("app.py"))
        row = rows_by_path(build_dataset(history(past_fix, future_fix), config()))["app.py"]
        self.assertEqual(row["past_bug_fixes"], 1)
        self.assertEqual(row["label"], 1)

    def test_files_created_after_snapshot_are_not_rows(self):
        new = commit("n1", CUTOFF + timedelta(days=1), "Add new", change("new.py", kind="ADD"))
        self.assertNotIn("new.py", rows_by_path(build_dataset(history(new), config())))

    def test_column_groups_are_disjoint(self):
        self.assertFalse(set(FEATURE_COLUMNS) & set(LABEL_COLUMNS))
        self.assertFalse(set(FEATURE_COLUMNS) & set(ID_COLUMNS))
        self.assertEqual(len(COLUMNS), len(set(COLUMNS)))
        for name in FEATURE_COLUMNS:
            self.assertNotIn("label", name)
            self.assertNotIn("future", name)

    def test_feature_values_are_as_of_snapshot(self):
        row = rows_by_path(build_dataset(history(), config()))["app.py"]
        self.assertEqual(row["commits"], 2)
        self.assertEqual(row["churn"], 13)
        self.assertEqual(row["age_days"], (CUTOFF - START).days)
        self.assertEqual(row["days_since_last_change"], (CUTOFF - START - timedelta(days=10)).days)
        self.assertEqual(row["author_hhi"], 1.0)


class RenameAndDeleteTests(unittest.TestCase):
    def test_rename_in_window_labels_original_path(self):
        rename = commit("r1", CUTOFF + timedelta(days=2), "Rename app",
                        change("core.py", 0, 0, "RENAME", old="app.py"))
        fix = commit("fix1", CUTOFF + timedelta(days=4), "Fix crash", change("core.py"))
        rows = rows_by_path(build_dataset(history(rename, fix), config()))
        self.assertEqual(rows["app.py"]["label"], 1)
        self.assertNotIn("core.py", rows)

    def test_rename_before_snapshot_uses_new_path(self):
        rename = commit("r1", CUTOFF - timedelta(days=2), "Rename app",
                        change("core.py", 0, 0, "RENAME", old="app.py"))
        rows = rows_by_path(build_dataset(history(rename), config()))
        self.assertIn("core.py", rows)
        self.assertNotIn("app.py", rows)

    def test_deleted_before_snapshot_is_not_a_row(self):
        delete = commit("d1", CUTOFF - timedelta(days=1), "Remove util",
                        change("util.py", 0, 5, "DELETE"))
        self.assertNotIn("util.py", rows_by_path(build_dataset(history(delete), config())))

    def test_deleted_in_window_is_flagged(self):
        delete = commit("d1", CUTOFF + timedelta(days=3), "Remove util",
                        change("util.py", 0, 5, "DELETE"))
        row = rows_by_path(build_dataset(history(delete), config()))["util.py"]
        self.assertEqual((row["label"], row["deleted_in_window"]), (0, 1))

    def test_fix_that_only_deletes_a_file_does_not_label_it(self):
        delete = commit("d1", CUTOFF + timedelta(days=3), "Fix crash by removing util",
                        change("util.py", 0, 5, "DELETE"))
        self.assertEqual(rows_by_path(build_dataset(history(delete), config()))["util.py"]["label"], 0)

    def test_alive_files(self):
        commits = [
            commit("a", START, "x", change("a.py", kind="ADD"), change("b.py", kind="ADD")),
            commit("b", START, "x", change("c.py", 0, 0, "RENAME", old="a.py")),
            commit("c", START, "x", change("b.py", 0, 1, "DELETE")),
        ]
        self.assertEqual(alive_files(commits), {"c.py"})


class EmptyAndInvalidTests(unittest.TestCase):
    def test_no_commits(self):
        dataset = build_dataset([], config())
        self.assertEqual(dataset.rows, [])
        self.assertEqual(dataset.metadata["rows"], 0)
        self.assertIn("empty", dataset.metadata["note"])

    def test_commits_without_dates_are_skipped_and_counted(self):
        undated = CommitRecord("u", "dev@x.com", None, (change("app.py"),), "Fix crash")
        dataset = build_dataset(history(undated), config())
        self.assertEqual(dataset.metadata["commits_skipped_missing_date"], 1)
        self.assertEqual(rows_by_path(dataset)["app.py"]["label"], 0)

    def test_invalid_path(self):
        with self.assertRaises(ValueError):
            build_dataset_for_repo("/definitely/not/a/repo")
        with tempfile.TemporaryDirectory() as plain_dir:
            with self.assertRaises(ValueError):
                build_dataset_for_repo(plain_dir)

    def test_filters_choose_rows(self):
        commits = history(commit("t", START + timedelta(days=1), "Add tests",
                                 change("tests/test_app.py", kind="ADD"),
                                 change("README.md", kind="ADD")))
        with_tests = rows_by_path(build_dataset(commits, config()))
        self.assertIn("tests/test_app.py", with_tests)
        self.assertNotIn("README.md", with_tests)
        without = rows_by_path(build_dataset(commits, config(include_tests=False)))
        self.assertNotIn("tests/test_app.py", without)


class SourceFileTests(unittest.TestCase):
    def test_only_source_files_by_default(self):
        added = commit("t", START + timedelta(days=1), "Add misc",
                       change("AUTHORS.rst", kind="ADD"), change(".gitignore", kind="ADD"),
                       change("setup.cfg", kind="ADD"), change("web/app.ts", kind="ADD"))
        fix = commit("fix1", CUTOFF + timedelta(days=5), "Fix crash",
                     change("app.py"), change("AUTHORS.rst"))
        rows = rows_by_path(build_dataset(history(added, fix), config()))
        self.assertEqual(set(rows), {"app.py", "util.py", "web/app.ts"})

        everything = rows_by_path(build_dataset(history(added, fix), config(source_extensions=None)))
        self.assertEqual(everything["AUTHORS.rst"]["label"], 1)
        self.assertIn(".gitignore", everything)


class CsvExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fix = commit("fix1", CUTOFF + timedelta(days=5), "Fix crash", change("app.py"))
        self.dataset = build_dataset(history(fix), config())

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_csv_and_metadata_in_new_directories(self):
        target = os.path.join(self.tmp.name, "nested", "dir", "data.csv")
        csv_path, meta_path = write_dataset(self.dataset, target)
        self.assertTrue(meta_path.name.endswith("data.meta.json"))
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            self.assertEqual(tuple(reader.fieldnames), COLUMNS)
            rows = list(reader)
        self.assertEqual(len(rows), len(self.dataset.rows))
        self.assertEqual({r["path"]: r["label"] for r in rows}, {"app.py": "1", "util.py": "0"})
        with open(meta_path) as f:
            meta = json.load(f)
        self.assertEqual(meta["feature_columns"], list(FEATURE_COLUMNS))
        self.assertEqual(meta["label_columns"], list(LABEL_COLUMNS))
        self.assertEqual(meta["positives"], 1)
        self.assertTrue(any("heuristic" in l for l in meta["limitations"]))

    def test_csv_is_reproducible(self):
        first, _ = write_dataset(self.dataset, os.path.join(self.tmp.name, "a.csv"))
        again = build_dataset(history(commit("fix1", CUTOFF + timedelta(days=5), "Fix crash",
                                             change("app.py"))), config())
        second, _ = write_dataset(again, os.path.join(self.tmp.name, "b.csv"))
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_incomplete_labels_are_blank_in_csv(self):
        commits = history(end=CUTOFF + timedelta(days=10))
        dataset = build_dataset(commits, config(include_incomplete=True))
        path, _ = write_dataset(dataset, os.path.join(self.tmp.name, "inc.csv"))
        with open(path, newline="") as f:
            labels = {r["label"] for r in csv.DictReader(f)}
        self.assertEqual(labels, {""})

    def test_refuses_to_write_inside_analyzed_repo(self):
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(repo)
        with self.assertRaises(ValueError):
            write_dataset(self.dataset, os.path.join(repo, "output", "d.csv"), repo_path=repo)
        self.assertFalse(os.path.exists(os.path.join(repo, "output")))


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class GitRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.cleanup()

    def test_empty_git_repository(self):
        dataset = build_dataset_for_repo(self.repo.path, config())
        self.assertEqual(dataset.rows, [])
        self.assertIsNone(dataset.metadata["head_commit"])

    def test_read_commits_records_renames_and_deletes(self):
        r = self.repo
        r.commit("Add", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
        r.git("mv", "a.py", "c.py")
        r.commit("Rename", remove=["b.py"])
        kinds = {(ch.change_type, ch.old_path, ch.path) for ch in read_commits(r.path)[-1].changes}
        self.assertEqual(kinds, {("RENAME", "a.py", "c.py"), ("DELETE", "b.py", "b.py")})

    def test_end_to_end_rename_then_fix(self):
        # TempRepo dates commits on consecutive days starting 2024-01-01
        r = self.repo
        r.commit("Add calculator", {"calc.py": "def sub(a, b):\n    return b - a\n",
                                    "io.py": "print(1)\n"})          # 01-01
        r.commit("Tweak io", {"io.py": "print(2)\n"})                 # 01-02
        r.git("mv", "calc.py", "maths.py")
        r.commit("Rename calc")                                       # 01-03
        r.commit("Fix incorrect sub result",
                 {"maths.py": "def sub(a, b):\n    return a - b\n"})  # 01-04
        for i in range(6):                                            # 01-05 .. 01-10
            r.commit(f"Add note {i}", {f"notes/n{i}.txt": "x\n"})

        cfg = DatasetConfig(horizon_days=5, snapshot_interval_days=5, first_snapshot=date(2024, 1, 2))
        dataset = build_dataset_for_repo(r.path, cfg)
        rows = rows_by_path(dataset, "2024-01-02")
        self.assertEqual(rows["calc.py"]["label"], 1)  # fixed after being renamed
        self.assertEqual(rows["io.py"]["label"], 0)
        self.assertIsNotNone(dataset.metadata["head_commit"])

        out = os.path.join(tempfile.mkdtemp(), "ds.csv")
        try:
            write_dataset(dataset, out, repo_path=r.path)
            self.assertTrue(os.path.exists(out))
        finally:
            shutil.rmtree(os.path.dirname(out))


if __name__ == "__main__":
    unittest.main()
