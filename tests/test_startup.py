"""Regression tests for startup configuration: docs availability, the uvicorn
factory entry point, and startup warnings.

Bug: started with `uvicorn reporisk.api.app:create_app --factory` and no .env,
/docs and /openapi.json returned a bare JSON 404, because REPORISK_ENV
defaults to production (docs disabled) and nothing said so.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from reporisk.api.app import create_app, startup_summary
    from reporisk.api.config import Settings, build_repositories

    HAS_API = True
except ImportError:  # pragma: no cover
    HAS_API = False

from tests.auth_helpers import TEST_EMAIL, TEST_PASSWORD

DOCS = ("/docs", "/openapi.json", "/redoc")


@unittest.skipUnless(HAS_API, "API dependencies are not installed")
class StartupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # Never read the developer's real .env from the working directory.
        self.env = {"REPORISK_ENV_FILE": str(self.tmp / "no.env"), "REPORISK_DB_PATH": str(self.tmp / "a.sqlite3"),
                    "REPORISK_DATA_DIR": str(self.tmp / "data")}

    def tearDown(self):
        self._tmp.cleanup()

    def factory_app(self, **extra):
        """Build the app exactly like `uvicorn reporisk.api.app:create_app --factory` does."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("REPORISK_")}
        env.update(self.env, **extra)
        with mock.patch.dict(os.environ, env, clear=True):
            return create_app()


class DocsAvailabilityTests(StartupTestCase):
    def test_production_default_explains_disabled_docs(self):
        app = self.factory_app()  # no REPORISK_ENV, as in the bug report
        self.assertTrue(app.state.settings.production)
        client = TestClient(app)
        for path in DOCS:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 404)
                error = response.json()["error"]
                self.assertEqual(error["code"], "docs_disabled")
                self.assertIn("REPORISK_ENV=development", error["message"])

    def test_development_serves_docs_and_schema(self):
        client = TestClient(self.factory_app(REPORISK_ENV="development", REPORISK_COOKIE_SECURE="false"))
        docs = client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("swagger", docs.text.lower())
        schema = client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        paths = schema.json()["paths"]
        for route in ("/api/auth/login", "/api/analyze", "/api/analysis/{analysis_id}/files"):
            self.assertIn(route, paths)

    def test_docs_do_not_bypass_authentication(self):
        client = TestClient(self.factory_app(REPORISK_ENV="development", REPORISK_COOKIE_SECURE="false"))
        self.assertEqual(client.get("/api/repositories").status_code, 401)
        self.assertEqual(client.post("/api/analyze", json={"repository_id": "x"}).status_code, 401)

    def test_factory_and_module_entry_points_agree(self):
        app = self.factory_app(REPORISK_ENV="development", REPORISK_COOKIE_SECURE="false")
        self.assertEqual((app.docs_url, app.openapi_url), ("/docs", "/openapi.json"))
        self.assertFalse(app.state.settings.cookie_secure)

    def test_env_file_is_read_by_the_factory(self):
        env_file = self.tmp / ".env"
        env_file.write_text("REPORISK_ENV=development\nREPORISK_COOKIE_SECURE=false\n")
        app = self.factory_app(REPORISK_ENV_FILE=str(env_file))
        self.assertFalse(app.state.settings.production)
        self.assertEqual(TestClient(app).get("/openapi.json").status_code, 200)


class StartupSummaryTests(StartupTestCase):
    def test_warns_about_missing_env_accounts_and_repositories(self):
        app = self.factory_app(REPORISK_REPOS=str(self.tmp / "missing-repo"))
        with mock.patch.dict(os.environ, {}, clear=True):
            lines = startup_summary(app.state.settings, app.state.auth)
        text = "\n".join(lines)
        self.assertIn("environment=production", lines[0])
        self.assertIn("REPORISK_ENV is not set", text)
        self.assertIn("no accounts exist", text)
        self.assertIn("is unavailable: directory does not exist", text)

    def test_quiet_when_configured(self):
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        settings = Settings(repositories=build_repositories([str(repo)]), db_path=self.tmp / "b.sqlite3",
                            data_dir=self.tmp / "data")
        app = create_app(settings)
        app.state.auth.create_user(TEST_EMAIL, TEST_PASSWORD)
        lines = startup_summary(settings, app.state.auth)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("repositories 1 of 1 available", lines[0])

    def test_summary_is_logged_on_startup(self):
        app = self.factory_app()
        with self.assertLogs("uvicorn.error", level="INFO") as logs, TestClient(app):
            pass
        self.assertTrue(any("RepoRisk API" in m for m in logs.output))
        self.assertTrue(any("no accounts exist" in m for m in logs.output))


class AdminProvisioningTests(StartupTestCase):
    """REPORISK_ADMIN_EMAIL/PASSWORD: accounts on hosts without a shell or persistent disk."""

    ADMIN = {"REPORISK_ENV": "development", "REPORISK_COOKIE_SECURE": "false",
             "REPORISK_ADMIN_EMAIL": "owner@example.com", "REPORISK_ADMIN_PASSWORD": "a long admin password"}

    def login(self, app, password="a long admin password"):
        return TestClient(app).post("/api/auth/login", json={"email": "owner@example.com", "password": password})

    def test_account_is_created_and_can_sign_in(self):
        app = self.factory_app(**self.ADMIN)
        self.assertEqual(self.login(app).status_code, 200)
        self.assertNotIn("a long admin password", repr(app.state.settings))

    def test_survives_a_wiped_database(self):
        self.factory_app(**self.ADMIN)
        os.remove(self.env["REPORISK_DB_PATH"])  # what a free-tier restart does to the disk
        self.assertEqual(self.login(self.factory_app(**self.ADMIN)).status_code, 200)

    def test_restart_keeps_sessions_unless_the_password_changes(self):
        first = self.factory_app(**self.ADMIN)
        client = TestClient(first)
        self.assertEqual(client.post("/api/auth/login", json={"email": "owner@example.com",
                                                             "password": "a long admin password"}).status_code, 200)
        same = self.factory_app(**self.ADMIN)
        self.assertEqual(TestClient(same, cookies=client.cookies).get("/api/auth/me").status_code, 200)

        rotated = self.factory_app(**{**self.ADMIN, "REPORISK_ADMIN_PASSWORD": "a rotated admin password"})
        self.assertEqual(TestClient(rotated, cookies=client.cookies).get("/api/auth/me").status_code, 401)
        self.assertEqual(self.login(rotated).status_code, 401)
        self.assertEqual(self.login(rotated, "a rotated admin password").status_code, 200)

    def test_invalid_configuration_refuses_to_start(self):
        from reporisk.api.config import ConfigError

        with self.assertRaises(ConfigError):
            self.factory_app(**{**self.ADMIN, "REPORISK_ADMIN_PASSWORD": "short"})
        with self.assertRaises(ConfigError):
            self.factory_app(REPORISK_ADMIN_EMAIL="owner@example.com")

    def test_not_set_means_no_account(self):
        app = self.factory_app(REPORISK_ENV="development", REPORISK_COOKIE_SECURE="false")
        self.assertEqual(app.state.auth.list_users(), [])


if __name__ == "__main__":
    unittest.main()

