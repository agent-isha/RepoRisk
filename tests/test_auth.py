"""Authentication tests: accounts, login, sessions, CSRF, logout and config safety."""

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from reporisk.api.app import create_app
    from reporisk.api.auth import (
        SESSION_COOKIE,
        AuthError,
        AuthStore,
        LoginThrottle,
        hash_password,
        verify_password,
    )
    from reporisk.api.config import ConfigError, Settings, read_env_file, settings_from_env
    from reporisk.api import users as users_cli

    HAS_API = True
except ImportError:  # pragma: no cover
    HAS_API = False

from tests.auth_helpers import TEST_EMAIL, TEST_PASSWORD, sign_in

PROTECTED = [
    ("get", "/api/repositories"),
    ("get", "/api/analyses"),
    ("get", "/api/analysis/abc"),
    ("get", "/api/analysis/abc/summary"),
    ("get", "/api/analysis/abc/files"),
    ("get", "/api/analysis/abc/predictions"),
    ("get", "/api/analysis/abc/bug-fixes"),
    ("get", "/api/auth/me"),
    ("post", "/api/analyze"),
]


@unittest.skipUnless(HAS_API, "API dependencies are not installed")
class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = Settings(data_dir=self.tmp / "data", db_path=self.tmp / "auth.sqlite3")
        self.app = create_app(self.settings)
        self.store = self.app.state.auth
        self.store.create_user(TEST_EMAIL, TEST_PASSWORD)
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def login(self, email=TEST_EMAIL, password=TEST_PASSWORD, client=None):
        return (client or self.client).post("/api/auth/login", json={"email": email, "password": password})

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code)
        return response.json()["error"]


class PasswordAndAccountTests(AuthTestCase):
    def test_passwords_are_hashed_with_argon2id(self):
        hashed = hash_password("a long enough password")
        self.assertTrue(hashed.startswith("$argon2id$"))
        self.assertTrue(verify_password(hashed, "a long enough password"))
        self.assertFalse(verify_password(hashed, "wrong password!!"))
        self.assertFalse(verify_password("not-a-hash", "anything at all"))

    def test_database_never_contains_plain_password(self):
        raw = (self.tmp / "auth.sqlite3").read_bytes()
        self.assertNotIn(TEST_PASSWORD.encode(), raw)

    def test_account_validation(self):
        for email, password in [("not-an-email", "long enough password"),
                                ("x@example.com", "short"),
                                ("longaddress@example.com", "longaddress@example.com")]:
            with self.subTest(email=email):
                with self.assertRaises(AuthError):
                    self.store.create_user(email, password)
        with self.assertRaises(AuthError):
            self.store.create_user(TEST_EMAIL.upper(), "another long password")  # emails are case-insensitive

    def test_no_default_accounts(self):
        empty = AuthStore(self.tmp / "empty.sqlite3")
        self.assertEqual(empty.list_users(), [])


class LoginTests(AuthTestCase):
    def test_successful_login_sets_secure_cookie(self):
        response = self.login(email="  Analyst@Example.com ")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["user"]["email"], TEST_EMAIL)
        self.assertTrue(body["csrf_token"])
        self.assertNotIn("password", response.text.lower())
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{SESSION_COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_session_token_is_stored_hashed(self):
        self.login()
        token = self.client.cookies.get(SESSION_COOKIE)
        with sqlite3.connect(self.tmp / "auth.sqlite3") as db:
            stored = [row[0] for row in db.execute("SELECT token_hash FROM sessions")]
        self.assertEqual(len(stored), 1)
        self.assertNotEqual(stored[0], token)
        self.assertNotIn(token, (self.tmp / "auth.sqlite3").read_bytes().decode("latin-1"))

    def test_invalid_credentials_share_one_message(self):
        wrong = self.assert_error(self.login(password="wrong password here"), 401, "invalid_credentials")
        unknown = self.assert_error(self.login(email="nobody@example.com"), 401, "invalid_credentials")
        self.assertEqual(wrong["message"], unknown["message"])
        self.assertNotIn("set-cookie", self.login(password="wrong password here").headers)

    def test_login_validation(self):
        for body in [{}, {"email": "a@b.co"}, {"email": "a@b.co", "password": ""},
                     {"email": "a@b.co", "password": "x", "remember": True}]:
            with self.subTest(body=body):
                self.assert_error(self.client.post("/api/auth/login", json=body), 422, "validation_error")

    def test_inactive_account_cannot_log_in(self):
        self.store.set_active(TEST_EMAIL, False)
        self.assert_error(self.login(), 401, "invalid_credentials")

    def test_repeated_failures_are_throttled(self):
        for _ in range(self.settings.login_max_failures):
            self.assert_error(self.login(password="wrong password here"), 401, "invalid_credentials")
        locked = self.login()  # even the right password is refused while locked
        self.assert_error(locked, 429, "too_many_attempts")
        self.assertGreater(int(locked.headers["retry-after"]), 0)

    def test_throttle_window_expires(self):
        now = [0.0]
        throttle = LoginThrottle(max_failures=2, lockout_minutes=1, clock=lambda: now[0])
        throttle.failure("a@b.co")
        throttle.failure("A@B.co")
        self.assertGreater(throttle.retry_after("a@b.co"), 0)
        now[0] = 61
        self.assertEqual(throttle.retry_after("a@b.co"), 0)
        throttle.failure("a@b.co")
        throttle.success("a@b.co")
        self.assertEqual(throttle.retry_after("a@b.co"), 0)


class SessionTests(AuthTestCase):
    def test_protected_endpoints_require_a_session(self):
        for method, url in PROTECTED:
            with self.subTest(url=url):
                kwargs = {"json": {"repository_id": "x"}} if method == "post" else {}
                self.assert_error(getattr(self.client, method)(url, **kwargs), 401, "not_authenticated")

    def test_health_stays_public(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_forged_cookie_is_rejected(self):
        self.client.cookies.set(SESSION_COOKIE, "forged-token")
        self.assert_error(self.client.get("/api/repositories"), 401, "not_authenticated")

    def test_me_returns_the_session(self):
        session = sign_in(self.client, self.app)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["email"], TEST_EMAIL)
        self.assertEqual(me.json()["csrf_token"], session["csrf_token"])
        self.assertEqual(self.client.get("/api/repositories").status_code, 200)

    def test_expired_session_is_rejected(self):
        sign_in(self.client, self.app)
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.tmp / "auth.sqlite3") as db:
            db.execute("UPDATE sessions SET expires_at = ?", (past,))
        self.assert_error(self.client.get("/api/auth/me"), 401, "not_authenticated")

    def test_deactivating_or_changing_password_ends_sessions(self):
        sign_in(self.client, self.app)
        self.store.set_password(TEST_EMAIL, "a brand new password")
        self.assert_error(self.client.get("/api/auth/me"), 401, "not_authenticated")
        sign_in(self.client, self.app, password="a brand new password")
        self.store.set_active(TEST_EMAIL, False)
        self.assert_error(self.client.get("/api/auth/me"), 401, "not_authenticated")

    def test_sessions_are_independent(self):
        other = TestClient(self.app)
        sign_in(self.client, self.app)
        sign_in(other, self.app)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(other.get("/api/auth/me").status_code, 200)


class CsrfAndOriginTests(AuthTestCase):
    def test_post_requires_csrf_token(self):
        sign_in(self.client, self.app)
        token = self.client.headers.pop("X-CSRF-Token")
        self.assert_error(self.client.post("/api/analyze", json={"repository_id": "x"}), 403, "csrf_failed")
        self.client.headers["X-CSRF-Token"] = "wrong"
        self.assert_error(self.client.post("/api/analyze", json={"repository_id": "x"}), 403, "csrf_failed")
        self.client.headers["X-CSRF-Token"] = token
        # passes CSRF; then fails the allowlist, proving the check order
        self.assert_error(self.client.post("/api/analyze", json={"repository_id": "x"}), 404, "repository_not_permitted")

    def test_foreign_origin_is_refused(self):
        sign_in(self.client, self.app)
        response = self.client.post("/api/analyze", json={"repository_id": "x"},
                                    headers={"Origin": "https://evil.example"})
        self.assert_error(response, 403, "origin_not_allowed")
        login = self.client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                                 headers={"Origin": "https://evil.example"})
        self.assert_error(login, 403, "origin_not_allowed")

    def test_configured_origin_is_allowed_with_credentials(self):
        response = self.client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                                    headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")

    def test_security_headers(self):
        headers = self.client.get("/health").headers
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])


class LogoutTests(AuthTestCase):
    def test_logout_revokes_the_session(self):
        sign_in(self.client, self.app)
        old_token = self.client.cookies.get(SESSION_COOKIE)
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.json(), {"status": "logged_out"})
        self.assertIn(f'{SESSION_COOKIE}=""', response.headers["set-cookie"])
        self.assert_error(self.client.get("/api/auth/me"), 401, "not_authenticated")
        # replaying the old cookie doesn't work either: the session is gone server-side
        replay = TestClient(self.app)
        replay.cookies.set(SESSION_COOKIE, old_token)
        self.assert_error(replay.get("/api/repositories"), 401, "not_authenticated")

    def test_logout_requires_csrf_when_signed_in(self):
        sign_in(self.client, self.app)
        del self.client.headers["X-CSRF-Token"]
        self.assert_error(self.client.post("/api/auth/logout"), 403, "csrf_failed")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_logout_without_session_is_harmless(self):
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)


class ConfigTests(unittest.TestCase):
    def test_production_is_the_default_and_uses_secure_cookies(self):
        settings = settings_from_env({})
        self.assertTrue(settings.production)
        self.assertTrue(settings.cookie_secure)

    def test_production_cannot_disable_secure_cookies(self):
        settings = settings_from_env({"REPORISK_ENV": "production", "REPORISK_COOKIE_SECURE": "false"})
        self.assertTrue(settings.cookie_secure)
        with self.assertRaises(ConfigError):
            Settings(environment="production", cookie_secure=False)

    def test_development_may_disable_secure_cookies(self):
        settings = settings_from_env({"REPORISK_ENV": "development", "REPORISK_COOKIE_SECURE": "false"})
        self.assertFalse(settings.cookie_secure)

    def test_invalid_values(self):
        with self.assertRaises(ConfigError):
            settings_from_env({"REPORISK_ENV": "staging"})
        with self.assertRaises(ConfigError):
            settings_from_env({"REPORISK_SESSION_HOURS": "soon"})

    @unittest.skipUnless(HAS_API, "API dependencies are not installed")
    def test_production_hides_docs_and_sets_secure_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Settings(environment="production", cookie_secure=True,
                                      db_path=Path(tmp) / "a.sqlite3", data_dir=Path(tmp)))
            client = TestClient(app, base_url="https://testserver")
            self.assertEqual(client.get("/docs").status_code, 404)
            app.state.auth.create_user(TEST_EMAIL, TEST_PASSWORD)
            response = client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
            self.assertIn("Secure", response.headers["set-cookie"])

    @unittest.skipUnless(HAS_API, "API dependencies are not installed")
    def test_production_serves_frontend_from_same_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<!doctype html><title>RepoRisk</title>")
            app = create_app(Settings(environment="production", cookie_secure=True, frontend_dist=dist,
                                      db_path=Path(tmp) / "a.sqlite3", data_dir=Path(tmp)))
            client = TestClient(app, base_url="https://testserver")
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("RepoRisk", page.text)
            self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
            # API routes still win over the static mount and stay protected
            self.assertEqual(client.get("/api/repositories").status_code, 401)
            with self.assertRaises(RuntimeError):
                create_app(Settings(frontend_dist=Path(tmp) / "missing", db_path=Path(tmp) / "b.sqlite3"))

    def test_env_file_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# comment\nREPORISK_ENV=development\nexport REPORISK_REPOS="/a:/b"\nBROKEN\n')
            self.assertEqual(read_env_file(path), {"REPORISK_ENV": "development", "REPORISK_REPOS": "/a:/b"})
            self.assertEqual(read_env_file(Path(tmp) / "missing"), {})


@unittest.skipUnless(HAS_API, "API dependencies are not installed")
class UsersCliTests(unittest.TestCase):
    def run_cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out), redirect_stderr(err):
            code = users_cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_account_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "users.sqlite3")
            code, out, _ = self.run_cli("--db", db, "create", "dev@example.com", "--password-stdin",
                                        stdin="a sufficiently long password\n")
            self.assertEqual((code, out.strip()), (0, "Created dev@example.com"))
            self.assertIn("dev@example.com", self.run_cli("--db", db, "list")[1])
            self.assertEqual(self.run_cli("--db", db, "deactivate", "dev@example.com")[0], 0)
            self.assertIn("inactive", self.run_cli("--db", db, "list")[1])
            self.assertEqual(self.run_cli("--db", db, "delete", "dev@example.com")[0], 0)

    def test_weak_password_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_cli("--db", str(Path(tmp) / "u.sqlite3"), "create", "dev@example.com",
                                        "--password-stdin", stdin="short\n")
            self.assertEqual(code, 2)
            self.assertIn("at least 12 characters", err)

    def test_interactive_prompts_must_match(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("getpass.getpass", side_effect=["first long password", "second long password"]):
            code, _, err = self.run_cli("--db", str(Path(tmp) / "u.sqlite3"), "create", "dev@example.com")
        self.assertEqual(code, 2)
        self.assertIn("do not match", err)


if __name__ == "__main__":
    unittest.main()
