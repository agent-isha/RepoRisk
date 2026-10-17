import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from reporisk.mining import (
    CommitRecord,
    FileChange,
    FileStats,
    compute_stats,
    parse_cutoff,
    rank_files,
    read_commits,
)

UTC = timezone.utc
CUTOFF = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)


def commit(date, *paths, author="a@x.com", added=1, deleted=0, sha=None):
    changes = tuple(FileChange(p, added, deleted) for p in paths)
    return CommitRecord(sha or f"{date.isoformat()}-{paths}", author, date, changes)


class EmptyHistoryTests(unittest.TestCase):
    def test_no_commits(self):
        analysis = compute_stats([])
        self.assertEqual(analysis.files, {})
        self.assertEqual(analysis.commit_count, 0)
        self.assertIsNone(analysis.cutoff)
        self.assertEqual(len(analysis.pair_counts), 0)

    def test_no_commits_with_cutoff(self):
        analysis = compute_stats([], CUTOFF)
        self.assertEqual(analysis.commit_count, 0)
        self.assertEqual(analysis.cutoff, CUTOFF)

    def test_all_commits_after_cutoff(self):
        analysis = compute_stats([commit(CUTOFF + timedelta(days=1), "a.py")], CUTOFF)
        self.assertEqual(analysis.files, {})
        self.assertEqual(analysis.commit_count, 0)

    def test_file_without_authors_has_no_hhi(self):
        self.assertIsNone(FileStats().author_hhi)
        self.assertEqual(FileStats().authors, set())


class RecentChurnBoundaryTests(unittest.TestCase):
    def churn_for(self, date):
        analysis = compute_stats([commit(date, "a.py", added=3, deleted=2)], CUTOFF)
        return analysis.files["a.py"].recent_churn

    def test_commit_at_cutoff_counts_in_every_window(self):
        self.assertEqual(self.churn_for(CUTOFF), {30: 5, 90: 5, 180: 5})

    def test_window_start_is_exclusive(self):
        self.assertEqual(self.churn_for(CUTOFF - timedelta(days=30)), {30: 0, 90: 5, 180: 5})
        self.assertEqual(self.churn_for(CUTOFF - timedelta(days=90)), {30: 0, 90: 0, 180: 5})
        self.assertEqual(self.churn_for(CUTOFF - timedelta(days=180)), {30: 0, 90: 0, 180: 0})

    def test_just_inside_window(self):
        inside = CUTOFF - timedelta(days=30) + timedelta(seconds=1)
        self.assertEqual(self.churn_for(inside), {30: 5, 90: 5, 180: 5})

    def test_commits_after_cutoff_are_ignored_everywhere(self):
        commits = [
            commit(CUTOFF - timedelta(days=1), "a.py", added=10),
            commit(CUTOFF + timedelta(seconds=1), "a.py", added=100, author="late@x.com"),
        ]
        analysis = compute_stats(commits, CUTOFF)
        stats = analysis.files["a.py"]
        self.assertEqual(analysis.commit_count, 1)
        self.assertEqual(stats.churn, 10)
        self.assertEqual(stats.recent_churn[30], 10)
        self.assertEqual(stats.authors, {"a@x.com"})

    def test_default_cutoff_is_latest_commit(self):
        latest = datetime(2024, 1, 10, tzinfo=UTC)
        commits = [commit(latest, "a.py"), commit(latest - timedelta(days=40), "a.py")]
        analysis = compute_stats(commits)
        self.assertEqual(analysis.cutoff, latest)
        self.assertEqual(analysis.commit_count, 2)
        self.assertEqual(analysis.files["a.py"].recent_churn, {30: 1, 90: 2, 180: 2})

    def test_timezones_compare_as_instants(self):
        # 13:00 at +02:00 is 11:00 UTC, before the 12:00 UTC cutoff
        plus_two = timezone(timedelta(hours=2))
        analysis = compute_stats([commit(datetime(2024, 6, 30, 13, tzinfo=plus_two), "a.py")], CUTOFF)
        self.assertEqual(analysis.commit_count, 1)

    def test_naive_cutoff_is_utc(self):
        analysis = compute_stats([commit(CUTOFF, "a.py")], datetime(2024, 6, 30, 12, 0))
        self.assertEqual(analysis.cutoff, CUTOFF)
        self.assertEqual(analysis.commit_count, 1)

    def test_parse_cutoff(self):
        self.assertEqual(
            parse_cutoff("2024-06-30"), datetime(2024, 6, 30, 23, 59, 59, 999999, tzinfo=UTC)
        )
        self.assertEqual(parse_cutoff("2024-06-30T12:00:00"), CUTOFF)
        self.assertEqual(parse_cutoff("2024-06-30T14:00:00+02:00"), CUTOFF)
        with self.assertRaises(ValueError):
            parse_cutoff("30/06/2024")


class ContributorConcentrationTests(unittest.TestCase):
    def test_repeated_author(self):
        day = timedelta(days=1)
        commits = [
            commit(CUTOFF - 4 * day, "a.py", author="alice@x.com"),
            commit(CUTOFF - 3 * day, "a.py", author="alice@x.com"),
            commit(CUTOFF - 2 * day, "a.py", author="alice@x.com"),
            commit(CUTOFF - day, "a.py", author="bob@x.com"),
        ]
        stats = compute_stats(commits).files["a.py"]
        self.assertEqual(stats.author_commits, {"alice@x.com": 3, "bob@x.com": 1})
        self.assertEqual(stats.authors, {"alice@x.com", "bob@x.com"})
        self.assertAlmostEqual(stats.author_hhi, 0.75**2 + 0.25**2)

    def test_single_author_is_fully_concentrated(self):
        commits = [commit(CUTOFF - timedelta(days=i), "a.py") for i in range(5)]
        self.assertEqual(compute_stats(commits).files["a.py"].author_hhi, 1.0)

    def test_even_split(self):
        commits = [
            commit(CUTOFF - timedelta(days=i), "a.py", author=f"dev{i}@x.com") for i in range(4)
        ]
        self.assertAlmostEqual(compute_stats(commits).files["a.py"].author_hhi, 0.25)


class CouplingTests(unittest.TestCase):
    def setUp(self):
        day = timedelta(days=1)
        self.analysis = compute_stats(
            [
                commit(CUTOFF - 3 * day, "a.py", "b.py", "c.py"),
                commit(CUTOFF - 2 * day, "a.py", "b.py"),
                commit(CUTOFF - day, "a.py", "d.py"),
            ]
        )

    def test_pair_counts_are_sparse(self):
        self.assertEqual(
            dict(self.analysis.pair_counts),
            {("a.py", "b.py"): 2, ("a.py", "c.py"): 1, ("b.py", "c.py"): 1, ("a.py", "d.py"): 1},
        )
        # (b.py, d.py) and (c.py, d.py) never changed together, so are not stored
        self.assertNotIn(("b.py", "d.py"), self.analysis.pair_counts)

    def test_per_file_coupling_uses_min_cochanges(self):
        a, b, c = (self.analysis.files[p] for p in ("a.py", "b.py", "c.py"))
        self.assertEqual((a.coupled_files, a.top_partner), (1, "b.py"))
        self.assertAlmostEqual(a.strongest_coupling, 2 / 3)
        self.assertEqual((b.coupled_files, b.top_partner, b.strongest_coupling), (1, "a.py", 1.0))
        self.assertEqual((c.coupled_files, c.top_partner, c.strongest_coupling), (0, None, 0.0))

    def test_min_cochanges_one(self):
        day = timedelta(days=1)
        analysis = compute_stats(
            [
                commit(CUTOFF - 2 * day, "a.py", "c.py"),
                commit(CUTOFF - day, "a.py", "b.py"),
            ],
            min_cochanges=1,
        )
        a = analysis.files["a.py"]
        self.assertEqual(a.coupled_files, 2)
        self.assertEqual(a.top_partner, "b.py")  # ties break alphabetically
        self.assertAlmostEqual(a.strongest_coupling, 0.5)

    def test_large_commits_skipped_for_coupling_only(self):
        analysis = compute_stats(
            [commit(CUTOFF, "a.py", "b.py", "c.py")], max_files_per_commit=2
        )
        self.assertEqual(analysis.skipped_large_commits, 1)
        self.assertEqual(len(analysis.pair_counts), 0)
        self.assertEqual(analysis.files["a.py"].commits, 1)
        self.assertEqual(analysis.files["c.py"].churn, 1)

    def test_duplicate_path_in_commit_does_not_self_pair(self):
        analysis = compute_stats([commit(CUTOFF, "a.py", "a.py", "b.py")], min_cochanges=1)
        self.assertEqual(dict(analysis.pair_counts), {("a.py", "b.py"): 1})


class FilteringStaysSeparateTests(unittest.TestCase):
    def test_noise_files_keep_raw_features(self):
        commits = [
            commit(CUTOFF - timedelta(days=1), "requests/models.py", "Pipfile.lock"),
            commit(CUTOFF, "requests/models.py", "Pipfile.lock"),
        ]
        analysis = compute_stats(commits)
        self.assertIn("Pipfile.lock", analysis.files)
        self.assertEqual(analysis.files["Pipfile.lock"].top_partner, "requests/models.py")
        ranked = [path for path, _ in rank_files(analysis.files)]
        self.assertEqual(ranked, ["requests/models.py"])
        self.assertIn("Pipfile.lock", analysis.files)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class ReadCommitsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.git("init", "-q")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, date="2024-06-01T10:00:00+00:00", email="alice@x.com"):
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
        }
        subprocess.run(["git", *args], cwd=self.repo, env=env, check=True, capture_output=True)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def test_reads_multi_file_commits(self):
        self.write("a.py", "1\n2\n")
        self.write("b.py", "1\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "first", date="2024-06-01T10:00:00+00:00")
        self.write("a.py", "1\n2\n3\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "second", date="2024-06-20T10:00:00+00:00", email="bob@x.com")

        commits = read_commits(self.repo)
        self.assertEqual(len(commits), 2)
        self.assertEqual({c.path for c in commits[0].changes}, {"a.py", "b.py"})

        analysis = compute_stats(commits, parse_cutoff("2024-06-30"))
        a = analysis.files["a.py"]
        self.assertEqual((a.commits, a.added, a.churn), (2, 3, 3))
        self.assertEqual(a.recent_churn, {30: 3, 90: 3, 180: 3})
        self.assertAlmostEqual(a.author_hhi, 0.5)
        self.assertEqual(dict(analysis.pair_counts), {("a.py", "b.py"): 1})


if __name__ == "__main__":
    unittest.main()
