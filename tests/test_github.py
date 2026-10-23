"""Tests for analyzing public GitHub repositories by URL.

Cloning is replaced by a local stand-in (a clone of the synthetic test
repository) so the tests run offline; everything after the clone is the real
analysis pipeline. One optional test clones from github.com when
REPORISK_NETWORK_TESTS=1 is set.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from reporisk.api import github
    from reporisk.api.app import create_app
    from reporisk.api.config import Settings
    from reporisk.api.github import CloneError, GitHubRepo, InvalidGitHubURL, parse_github_url
    from reporisk.api.jobs import CloneLimits, JobManager, QueueFull
    from reporisk.api.schemas import AnalysisOptions

    HAS_API = True
except ImportError:  # pragma: no cover
    HAS_API = False

from tests import synthetic_repo
from tests.auth_helpers import sign_in

HAS_GIT = shutil.which("git") is not None
QUICK = {"run_szz": False, "train_model": False}


class UrlValidationTests(unittest.TestCase):
    @unittest.skipUnless(HAS_API, "API dependencies are not installed")
    def test_valid_urls(self):
        for url, expected in [
            ("https://github.com/psf/requests", "psf/requests"),
            ("https://github.com/psf/requests/", "psf/requests"),
            ("https://github.com/psf/requests.git", "psf/requests"),
            ("  https://GitHub.com/psf/requests  ", "psf/requests"),
            ("https://github.com/psf/requests\n", "psf/requests"),  # pasted with a trailing newline
            ("https://www.github.com/psf/requests", "psf/requests"),
            ("https://github.com:443/a-b/c.d_e", "a-b/c.d_e"),
        ]:
            with self.subTest(url=url):
                repo = parse_github_url(url)
                self.assertEqual(repo.full_name, expected)
                self.assertEqual(repo.clone_url, f"https://github.com/{expected}.git")

    @unittest.skipUnless(HAS_API, "API dependencies are not installed")
    def test_rejected_urls(self):
        for url in [
            "", "   ", "psf/requests", "github.com/psf/requests",
            "http://github.com/psf/requests",                 # not https
            "git@github.com:psf/requests.git",                # ssh
            "ssh://git@github.com/psf/requests",
            "file:///etc/passwd", "/Users/me/repo", "../repo",  # local paths
            "https://gitlab.com/psf/requests",                # other hosts
            "https://github.com.evil.example/psf/requests",
            "https://evil.example/github.com/psf/requests",
            "https://127.0.0.1/psf/requests", "https://localhost/psf/requests",
            "https://169.254.169.254/latest/meta-data",        # internal destinations
            "https://user:token@github.com/psf/requests",      # credentials
            "https://github.com@evil.example/psf/requests",
            "https://github.com:8443/psf/requests",            # other port
            "https://github.com/psf",                          # no repository
            "https://github.com/psf/requests/tree/main",       # not the repository root
            "https://github.com/psf/requests?tab=readme",
            "https://github.com/psf/requests#readme",
            "https://github.com/-psf/requests", "https://github.com/psf/..",
            "https://github.com/psf/re quests", "https://github.com/psf/req\nuests",  # whitespace inside
            "https://github.com/" + "a" * 40 + "/repo",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(InvalidGitHubURL):
                    parse_github_url(url)


@unittest.skipUnless(HAS_API, "API dependencies are not installed")
class CloneCommandTests(unittest.TestCase):
    def setUp(self):
        self.repo = GitHubRepo("psf", "requests")
        self.dest = Path(tempfile.mkdtemp()) / "repo"

    def tearDown(self):
        shutil.rmtree(self.dest.parent, ignore_errors=True)

    def test_runs_git_safely(self):
        with mock.patch.object(github.subprocess, "run") as run:
            github.clone_repository(self.repo, self.dest, timeout=42)
        args, kwargs = run.call_args
        cmd = args[0]
        self.assertIsInstance(cmd, list)                # argument list, never a shell string
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 42)
        self.assertEqual(cmd[-3:], ["--", "https://github.com/psf/requests.git", str(self.dest)])
        for flag in ("credential.helper=", "protocol.allow=never", "protocol.https.allow=always",
                     "http.followRedirects=false", "--no-checkout"):
            self.assertIn(flag, cmd)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_timeout(self):
        with mock.patch.object(github.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)):
            with self.assertRaisesRegex(CloneError, "longer than 5 seconds"):
                github.clone_repository(self.repo, self.dest, timeout=5)

    def test_missing_or_private_repository(self):
        error = subprocess.CalledProcessError(128, "git", stderr="remote: Repository not found.\nfatal: ...")
        with mock.patch.object(github.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(CloneError, "not found on GitHub, or it is not public"):
                github.clone_repository(self.repo, self.dest, timeout=5)

    def test_network_failure_message_has_no_paths(self):
        error = subprocess.CalledProcessError(128, "git", stderr=f"fatal: unable to access {self.dest}: Could not resolve host")
        with mock.patch.object(github.subprocess, "run", side_effect=error):
            with self.assertRaises(CloneError) as ctx:
                github.clone_repository(self.repo, self.dest, timeout=5)
        self.assertNotIn(str(self.dest), str(ctx.exception))


@unittest.skipUnless(HAS_API and HAS_GIT, "API dependencies or git are not installed")
class GitHubJobTests(unittest.TestCase):
    """JobManager with a local stand-in for GitHub; the analysis pipeline is real."""

    @classmethod
    def setUpClass(cls):
        cls._src = tempfile.TemporaryDirectory()
        cls.source = synthetic_repo.build(str(Path(cls._src.name) / "source"))

    @classmethod
    def tearDownClass(cls):
        cls._src.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.clone_dir = self.tmp / "clones"
        self.clones = []

    def tearDown(self):
        self._tmp.cleanup()

    def fake_clone(self, repo, dest, timeout):
        # Same flags as the real clone (no working tree), but from the local synthetic repo.
        self.clones.append(Path(dest))
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--", self.source, str(dest)],
                       check=True, capture_output=True)

    def manager(self, cloner=None, **limits):
        return JobManager(self.tmp / "data", clone_limits=CloneLimits(self.clone_dir, **limits),
                          cloner=cloner or self.fake_clone, max_queued=limits.pop("max_queued", None))

    def assert_clones_removed(self):
        self.assertTrue(self.clone_dir.is_dir())
        self.assertEqual(list(self.clone_dir.iterdir()), [], "temporary clone folder was not removed")

    def run_job(self, jobs, url="https://github.com/example/demo", **options):
        job = jobs.submit_github(parse_github_url(url), AnalysisOptions(**{**QUICK, **options}))
        jobs.wait(job.id, timeout=300)
        return job

    def test_successful_analysis_and_cleanup(self):
        jobs = self.manager()
        job = self.run_job(jobs)
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(job.status_dict()["repository_name"], "example/demo")
        self.assertEqual(job.status_dict()["repository_id"], "github:example/demo")
        summary = job.result.summary
        self.assertGreater(summary["commits_analyzed"], 50)
        self.assertEqual(summary["files_analyzed"], 5)
        self.assertGreater(summary["bug_fix_counts"]["likely_bug_fix"], 0)
        self.assertTrue(self.clones[0].is_relative_to(self.clone_dir) if hasattr(Path, "is_relative_to")
                        else str(self.clones[0]).startswith(str(self.clone_dir)))
        self.assert_clones_removed()

    def test_full_pipeline_with_szz_and_model(self):
        job = self.run_job(self.manager(), run_szz=True, train_model=True, horizon_days=30, snapshot_interval_days=30)
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual(job.result.summary["szz"]["status"], "completed")
        self.assertEqual(job.result.summary["dataset"]["status"], "built")
        self.assert_clones_removed()

    def test_clone_failure_cleans_up(self):
        def failing(repo, dest, timeout):
            Path(dest).mkdir(parents=True)
            (Path(dest) / "partial").write_text("half-downloaded")
            raise CloneError("example/demo was not found on GitHub, or it is not public.")

        job = self.run_job(self.manager(cloner=failing))
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "example/demo was not found on GitHub, or it is not public.")
        self.assert_clones_removed()

    def test_clone_timeout_through_the_real_clone_function(self):
        with mock.patch.object(github.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)):
            job = self.run_job(self.manager(cloner=github.clone_repository, timeout_seconds=1))
        self.assertEqual(job.status, "failed")
        self.assertIn("took longer than 1 seconds", job.error)
        self.assert_clones_removed()

    def test_analysis_failure_hides_paths_and_cleans_up(self):
        seen = {}

        def broken_runner(path, *_args):
            seen["path"] = path
            raise RuntimeError(f"cannot read {path}/.git/objects")

        jobs = JobManager(self.tmp / "data", runner=broken_runner,
                          clone_limits=CloneLimits(self.clone_dir), cloner=self.fake_clone)
        job = self.run_job(jobs)
        self.assertEqual(job.status, "failed")
        self.assertNotIn(seen["path"], job.error)
        self.assertNotIn(str(self.clone_dir), job.error)
        self.assertIn("<server path>", job.error)
        self.assert_clones_removed()

    def test_commit_and_size_limits(self):
        job = self.run_job(self.manager(max_commits=5))
        self.assertEqual(job.status, "failed")
        self.assertIn("above this server's 5 limit", job.error)
        job = self.run_job(self.manager(max_repo_mb=0.0001))
        self.assertEqual(job.status, "failed")
        self.assertIn("MB limit", job.error)
        self.assert_clones_removed()

    def test_concurrent_jobs_use_separate_folders(self):
        jobs = JobManager(self.tmp / "data", max_workers=2, clone_limits=CloneLimits(self.clone_dir),
                          cloner=self.fake_clone)
        first = jobs.submit_github(parse_github_url("https://github.com/example/one"), AnalysisOptions(**QUICK))
        second = jobs.submit_github(parse_github_url("https://github.com/example/two"), AnalysisOptions(**QUICK))
        jobs.wait(first.id, timeout=300)
        jobs.wait(second.id, timeout=300)
        self.assertEqual((first.status, second.status), ("completed", "completed"), (first.error, second.error))
        self.assertEqual(len({p.parent for p in self.clones}), 2)
        self.assertEqual(first.result.summary["repository"]["name"], "example/one")
        self.assertEqual(second.result.summary["repository"]["name"], "example/two")
        self.assert_clones_removed()

    def test_queue_limit(self):
        release = threading.Event()

        def slow_clone(repo, dest, timeout):
            release.wait(10)
            raise CloneError("stopped")

        jobs = JobManager(self.tmp / "data", max_queued=1, clone_limits=CloneLimits(self.clone_dir),
                          cloner=slow_clone)
        first = jobs.submit_github(parse_github_url("https://github.com/example/one"), AnalysisOptions(**QUICK))
        with self.assertRaises(QueueFull):
            jobs.submit_github(parse_github_url("https://github.com/example/two"), AnalysisOptions(**QUICK))
        release.set()
        jobs.wait(first.id, timeout=10)
        jobs.submit_github(parse_github_url("https://github.com/example/two"), AnalysisOptions(**QUICK))  # space again


@unittest.skipUnless(HAS_API and HAS_GIT, "API dependencies or git are not installed")
class GitHubApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._src = tempfile.TemporaryDirectory()
        cls.source = synthetic_repo.build(str(Path(cls._src.name) / "source"))

    @classmethod
    def tearDownClass(cls):
        cls._src.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        settings = Settings(data_dir=self.tmp / "data", db_path=self.tmp / "auth.sqlite3",
                            clone_dir=self.tmp / "clones", max_queued_analyses=2)
        self.app = create_app(settings)  # no REPORISK_REPOS needed for GitHub URLs
        self.app.state.jobs._cloner = lambda repo, dest, timeout: subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", "--", self.source, str(dest)], check=True, capture_output=True)
        self.client = TestClient(self.app)

    def tearDown(self):
        for job in self.app.state.jobs.list():  # let background jobs finish before deleting their folders
            try:
                self.app.state.jobs.wait(job.id, timeout=60)
            except Exception:
                pass
        self._tmp.cleanup()

    def post(self, body, client=None):
        return (client or self.client).post("/api/analyze", json=body)

    def test_requires_sign_in_and_csrf(self):
        body = {"github_url": "https://github.com/psf/requests", "options": QUICK}
        self.assertEqual(self.post(body).json()["error"]["code"], "not_authenticated")
        sign_in(self.client, self.app)
        token = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.post(body).json()["error"]["code"], "csrf_failed")
        self.client.headers["X-CSRF-Token"] = token
        self.assertEqual(self.post(body).status_code, 202)

    def test_invalid_urls_are_rejected_with_clear_messages(self):
        sign_in(self.client, self.app)
        cases = [
            ({"github_url": "http://github.com/psf/requests"}, "Only https:// GitHub URLs are supported."),
            ({"github_url": "https://gitlab.com/psf/requests"}, "Only repositories on github.com are supported."),
            ({"github_url": "/Users/me/repo"}, "Only https:// GitHub URLs are supported."),
            ({}, "Provide either github_url or repository_id."),
            ({"github_url": "https://github.com/a/b", "repository_id": "x"}, "Provide either github_url or repository_id."),
        ]
        for body, message in cases:
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual(response.status_code, 422)
                error = response.json()["error"]
                self.assertEqual(error["code"], "validation_error")
                self.assertEqual(error["details"][0]["message"], message)

    def test_end_to_end_results_through_existing_endpoints(self):
        sign_in(self.client, self.app)
        started = self.post({"github_url": " https://github.com/example/demo.git ", "options": QUICK})
        self.assertEqual(started.status_code, 202, started.text)
        job = started.json()
        self.assertEqual(job["repository_name"], "example/demo")
        self.app.state.jobs.wait(job["id"], timeout=300)

        status = self.client.get(f"/api/analysis/{job['id']}").json()
        self.assertEqual(status["status"], "completed", status["error"])
        summary = self.client.get(f"/api/analysis/{job['id']}/summary").json()
        self.assertEqual(summary["repository"]["name"], "example/demo")
        files = self.client.get(f"/api/analysis/{job['id']}/files").json()
        self.assertEqual(files["total"], 5)
        everything = "".join(self.client.get(f"/api/analysis/{job['id']}{p}").text
                             for p in ("", "/summary", "/files", "/bug-fixes", "/predictions"))
        self.assertNotIn(str(self.tmp), everything)  # no server paths in any response
        self.assertEqual(list((self.tmp / "clones").iterdir()), [])

    def test_too_many_analyses(self):
        sign_in(self.client, self.app)
        release = threading.Event()
        self.app.state.jobs._cloner = lambda repo, dest, timeout: release.wait(10) or (_ for _ in ()).throw(CloneError("stopped"))
        body = {"github_url": "https://github.com/example/demo", "options": QUICK}
        self.assertEqual(self.post(body).status_code, 202)
        self.assertEqual(self.post(body).status_code, 202)
        third = self.post(body)
        release.set()
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["error"]["code"], "too_many_analyses")

    def test_configured_repositories_still_work(self):
        self.assertEqual(self.client.get("/api/repositories").status_code, 401)
        sign_in(self.client, self.app)
        self.assertEqual(self.client.get("/api/repositories").json(), [])
        response = self.post({"repository_id": "nope"})
        self.assertEqual(response.json()["error"]["code"], "repository_not_permitted")


@unittest.skipUnless(os.environ.get("REPORISK_NETWORK_TESTS") == "1" and HAS_API and HAS_GIT,
                     "set REPORISK_NETWORK_TESTS=1 to clone from github.com")
class RealGitHubCloneTests(unittest.TestCase):
    def test_clone_public_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            repo = parse_github_url("https://github.com/octocat/Hello-World")
            github.clone_repository(repo, dest, timeout=120)
            github.check_limits(repo, dest, max_mb=100, max_commits=1000)
            self.assertGreater(github.commit_count(dest), 0)

    def test_missing_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = parse_github_url("https://github.com/octocat/this-repository-does-not-exist-reporisk")
            with self.assertRaisesRegex(CloneError, "not found on GitHub"):
                github.clone_repository(repo, Path(tmp) / "repo", timeout=60)


if __name__ == "__main__":
    unittest.main()
