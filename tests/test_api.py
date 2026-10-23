"""API tests: FastAPI TestClient against real temporary git repositories.

Run with pytest (pytest tests/test_api.py) or unittest. Skipped when the API
dependencies (requirements-api.txt) are not installed.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient

    from reporisk.api import schemas
    from reporisk.api.app import create_app
    from reporisk.api.config import Settings, build_repositories, settings_from_env

    HAS_API = True
except ImportError:  # pragma: no cover
    HAS_API = False

from reporisk import model
from tests import synthetic_repo
from tests.auth_helpers import sign_in

HAS_GIT = shutil.which("git") is not None
FIXTURES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "test" / "fixtures"
FAST = {"run_szz": True, "train_model": True, "horizon_days": 30, "snapshot_interval_days": 30}


@unittest.skipUnless(HAS_API, "API dependencies are not installed (pip install -r requirements-api.txt)")
class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo_path = self.tmp / "demo-repo"
        self.not_git = self.tmp / "plain-dir"
        self.not_git.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def client(self, paths=None, signed_in=True):
        """A TestClient for a fresh app; signed in (with the CSRF header set) by default."""
        paths = [str(self.repo_path), str(self.not_git), str(self.tmp / "missing")] if paths is None else paths
        settings = Settings(repositories=build_repositories(paths), data_dir=self.tmp / "data",
                            db_path=self.tmp / "auth.sqlite3")
        self.app = create_app(settings)
        client = TestClient(self.app)
        if signed_in:
            sign_in(client, self.app)
        return client

    def run_analysis(self, client, options=None, repo_id="demo-repo"):
        response = client.post("/api/analyze", json={"repository_id": repo_id, "options": options or FAST})
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.app.state.jobs.wait(job["id"], timeout=300)
        return job["id"]

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        return body["error"]


class HealthAndConfigTests(ApiTestCase):
    def test_health(self):
        body = self.client().get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["ml_available"], model.LogisticRegression is not None)
        self.assertEqual(body["repositories_configured"], 3)

    def test_repositories_report_availability(self):
        repos = {r["id"]: r for r in self.client().get("/api/repositories").json()}
        self.assertEqual(repos["plain-dir"]["reason"], "not a git repository")
        self.assertEqual(repos["missing"]["reason"], "directory does not exist")
        self.assertFalse(repos["demo-repo"]["available"])  # not created yet in this test

    def test_settings_from_env(self):
        env = {"REPORISK_REPOS": os.pathsep.join([str(self.not_git), str(self.not_git)]),
               "REPORISK_CORS_ORIGINS": "http://localhost:3000"}
        settings = settings_from_env(env)
        self.assertEqual([r.id for r in settings.repositories], ["plain-dir", "plain-dir-2"])
        self.assertEqual(settings.cors_origins, ("http://localhost:3000",))
        self.assertEqual(settings_from_env({}).repositories, ())

    def test_cors_only_for_configured_origin(self):
        client = self.client()
        allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:5173")
        other = client.get("/health", headers={"Origin": "http://evil.example"})
        self.assertNotIn("access-control-allow-origin", other.headers)


class ValidationTests(ApiTestCase):
    def test_request_validation(self):
        client = self.client()
        for body in [{}, {"repository_id": ""}, {"repository_id": "x", "path": "/etc"},
                     {"repository_id": "x", "options": {"horizon_days": 1}},
                     {"repository_id": "x", "options": {"unknown": True}}]:
            with self.subTest(body=body):
                error = self.assert_error(client.post("/api/analyze", json=body), 422, "validation_error")
                self.assertIsInstance(error["details"], list)

    def test_paths_are_never_accepted(self):
        client = self.client()
        self.assert_error(client.post("/api/analyze", json={"repository_id": "/etc"}),
                          404, "repository_not_permitted")
        self.assert_error(client.post("/api/analyze", json={"repository_id": "../demo-repo"}),
                          404, "repository_not_permitted")

    def test_invalid_repository(self):
        client = self.client()
        self.assert_error(client.post("/api/analyze", json={"repository_id": "plain-dir"}),
                          409, "repository_unavailable")
        self.assert_error(client.post("/api/analyze", json={"repository_id": "missing"}),
                          409, "repository_unavailable")

    def test_unknown_analysis_and_route(self):
        client = self.client()
        for url in ["/api/analysis/nope", "/api/analysis/nope/summary", "/api/analysis/nope/files",
                    "/api/analysis/nope/predictions", "/api/analysis/nope/bug-fixes"]:
            with self.subTest(url=url):
                self.assert_error(client.get(url), 404, "analysis_not_found")
        self.assert_error(client.get("/api/unknown"), 404, "not_found")

    def test_results_before_completion(self):
        client = self.client()
        job = self.app.state.jobs  # submit a job whose runner blocks until released
        import threading

        release = threading.Event()

        def slow_runner(*_args):
            release.wait(10)
            raise RuntimeError("stopped")

        job._runner = slow_runner
        from reporisk.api.config import RepositoryConfig

        repo = RepositoryConfig("demo", "demo", self.tmp, True)
        submitted = job.submit(repo, schemas.AnalysisOptions())
        error = self.assert_error(client.get(f"/api/analysis/{submitted.id}/summary"), 409, "analysis_not_ready")
        self.assertIn(error["details"]["status"], ("queued", "running"))
        release.set()
        job.wait(submitted.id, timeout=10)
        self.assertEqual(client.get(f"/api/analysis/{submitted.id}").json()["status"], "failed")
        failed = self.assert_error(client.get(f"/api/analysis/{submitted.id}/files"), 409, "analysis_failed")
        self.assertIn("stopped", failed["message"])


@unittest.skipUnless(HAS_GIT, "git is not installed")
class AnalysisTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        synthetic_repo.build(str(self.repo_path))

    def test_full_analysis(self):
        client = self.client()
        analysis_id = self.run_analysis(client)
        status = client.get(f"/api/analysis/{analysis_id}").json()
        self.assertEqual(status["status"], "completed", status)
        self.assertEqual(status["stage_index"], status["stage_count"])
        self.assertIn(analysis_id, [a["id"] for a in client.get("/api/analyses").json()])

        summary = client.get(f"/api/analysis/{analysis_id}/summary").json()
        self.assertGreater(summary["commits_analyzed"], 50)
        self.assertEqual(summary["files_analyzed"], 5)  # src/*.py; tests and README excluded
        self.assertEqual(sum(summary["risk_counts"].values()), 5)
        self.assertGreater(summary["bug_fix_counts"]["likely_bug_fix"], 0)
        self.assertEqual(summary["szz"]["status"], "completed")
        self.assertEqual(summary["dataset"]["status"], "built")
        self.assertEqual(len(summary["risk_factor_definitions"]), 6)

        files = client.get(f"/api/analysis/{analysis_id}/files").json()
        self.assertEqual(files["total"], 5)
        paths = [f["path"] for f in files["items"]]
        self.assertTrue(all(p.startswith("src/") for p in paths))
        core = next(f for f in files["items"] if f["path"] == "src/core.py")
        self.assertGreater(core["bug_fix_commits"], 0)

        bug_fixes = client.get(f"/api/analysis/{analysis_id}/bug-fixes?outcome=likely_bug_fix").json()
        self.assertEqual(bug_fixes["total"], summary["bug_fix_counts"]["likely_bug_fix"])
        traced = [b for b in bug_fixes["items"] if b["szz_records"]]
        self.assertTrue(traced, "synthetic fixes change existing lines, so SZZ should find candidates")
        self.assertTrue(traced[0]["szz_records"][0]["limitations"])
        self.assertTrue(bug_fixes["limitations"])

        predictions = client.get(f"/api/analysis/{analysis_id}/predictions").json()
        self.assertIn("not confirmed defects", predictions["label_note"])
        if model.LogisticRegression is None:
            self.assertEqual(predictions["status"], "unavailable")
            self.assertIn("scikit-learn", predictions["reason"])
            self.assertEqual(predictions["predictions"], [])
        else:
            self.assertEqual(predictions["status"], "available", predictions["reason"])
            probabilities = [p["probability"] for p in predictions["predictions"]]
            self.assertTrue(all(0 <= p <= 1 for p in probabilities))
            self.assertEqual(probabilities, sorted(probabilities, reverse=True))
            self.assertEqual({p["path"] for p in predictions["predictions"]}, set(paths))
            self.assertIsNotNone(predictions["evaluation"])

        # generated files land in the data dir, never in the analyzed repo
        self.assertTrue((self.tmp / "data" / analysis_id / "dataset.csv").exists())
        self.assertFalse((self.repo_path / "output").exists())

    def test_files_query_parameters(self):
        client = self.client()
        analysis_id = self.run_analysis(client, {"run_szz": False, "train_model": False})
        url = f"/api/analysis/{analysis_id}/files"
        by_path = client.get(url, params={"sort": "path", "order": "asc"}).json()["items"]
        self.assertEqual([f["path"] for f in by_path], sorted(f["path"] for f in by_path))
        self.assertEqual(client.get(url, params={"search": "CORE"}).json()["total"], 1)
        page = client.get(url, params={"limit": 2, "offset": 1}).json()
        self.assertEqual((page["total"], len(page["items"]), page["offset"]), (5, 2, 1))
        self.assert_error(client.get(url, params={"sort": "nonsense"}), 422, "validation_error")
        self.assert_error(client.get(url, params={"limit": 0}), 422, "validation_error")

    def test_disabled_stages_are_reported_not_invented(self):
        client = self.client()
        analysis_id = self.run_analysis(client, {"run_szz": False, "train_model": False})
        summary = client.get(f"/api/analysis/{analysis_id}/summary").json()
        self.assertEqual(summary["szz"]["status"], "skipped")
        self.assertEqual(summary["dataset"]["status"], "skipped")
        predictions = client.get(f"/api/analysis/{analysis_id}/predictions").json()
        self.assertEqual(predictions["status"], "unavailable")
        self.assertEqual(predictions["predictions"], [])
        self.assertIsNone(predictions.get("evaluation"))
        fixes = client.get(f"/api/analysis/{analysis_id}/bug-fixes").json()
        self.assertTrue(all(b["szz_status"] == "skipped" and not b["szz_records"] for b in fixes["items"]))

    def test_model_unavailable_without_scikit_learn(self):
        client = self.client()
        from unittest import mock

        with mock.patch("reporisk.api.engine.LogisticRegression", None), \
             mock.patch.object(model, "LogisticRegression", None):
            analysis_id = self.run_analysis(client, {**FAST, "run_szz": False})
        predictions = client.get(f"/api/analysis/{analysis_id}/predictions").json()
        self.assertEqual(predictions["status"], "unavailable")
        self.assertIn("scikit-learn is not installed", predictions["reason"])
        self.assertEqual(predictions["predictions"], [])

    def test_not_enough_history_for_model(self):
        client = self.client()
        options = {**FAST, "run_szz": False, "horizon_days": 365, "snapshot_interval_days": 365}
        analysis_id = self.run_analysis(client, options)
        predictions = client.get(f"/api/analysis/{analysis_id}/predictions").json()
        self.assertEqual(predictions["status"], "unavailable")
        self.assertTrue(predictions["reason"])
        self.assertIsNone(predictions["evaluation"])
        self.assertTrue(predictions["evaluation_unavailable_reason"])

    def test_empty_repository(self):
        empty = self.tmp / "empty-repo"
        empty.mkdir()
        synthetic_repo._git(str(empty), "init", "-q")
        client = self.client([str(empty)])
        analysis_id = self.run_analysis(client, FAST, repo_id="empty-repo")
        self.assertEqual(client.get(f"/api/analysis/{analysis_id}").json()["status"], "completed")
        summary = client.get(f"/api/analysis/{analysis_id}/summary").json()
        self.assertEqual((summary["commits_analyzed"], summary["files_analyzed"]), (0, 0))
        self.assertIsNone(summary["repository"]["head_commit"])
        self.assertEqual(client.get(f"/api/analysis/{analysis_id}/predictions").json()["status"], "unavailable")


@unittest.skipUnless(HAS_API, "API dependencies are not installed")
class FrontendFixtureContractTests(unittest.TestCase):
    """The frontend's test fixtures must stay valid API responses."""

    SCHEMAS = {
        "health.json": "Health",
        "repositories.json": "Repository[]",
        "analysis-status.json": "AnalysisStatus",
        "summary.json": "Summary",
        "files.json": "FilesPage",
        "predictions.json": "PredictionsResponse",
        "predictions-unavailable.json": "PredictionsResponse",
        "bug-fixes.json": "BugFixesPage",
        "session.json": "SessionInfo",
    }

    def test_fixtures_match_schemas(self):
        if not FIXTURES.is_dir():
            self.skipTest("frontend fixtures not generated yet")
        for name, schema in self.SCHEMAS.items():
            with self.subTest(fixture=name):
                data = json.loads((FIXTURES / name).read_text())
                model_name = schema.rstrip("[]")
                validator = getattr(schemas, model_name)
                for item in (data if schema.endswith("[]") else [data]):
                    validator.model_validate(item)


if __name__ == "__main__":
    unittest.main()
