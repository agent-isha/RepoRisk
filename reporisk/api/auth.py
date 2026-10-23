"""Accounts, password hashing and server-side sessions.

Design:
- Passwords are hashed with Argon2id (argon2-cffi); plain passwords are never stored.
- A session is a random 256-bit token sent in an HttpOnly, SameSite=Strict cookie.
  The database stores only the SHA-256 of the token, so a copy of the database
  can't be used to log in. Logging out deletes the row: revocation is immediate.
- Each session has its own CSRF token. The browser keeps it in memory and sends
  it in the X-CSRF-Token header on state-changing requests; the server compares
  it with the stored value. Unsafe requests from foreign origins are refused too.
- Repeated failed logins for an email are throttled.

Storage is SQLite via the standard library (one file, see REPORISK_DB_PATH).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

SESSION_COOKIE = "reporisk_session"
CSRF_HEADER = "X-CSRF-Token"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()  # Argon2id with argon2-cffi's recommended parameters
# Verified against when the email is unknown, so both paths take similar time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


class AuthError(ValueError):
    """Invalid input for an account operation."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    is_active: bool
    created_at: str
    last_login_at: str | None = None


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    email = normalize_email(email)
    if not EMAIL_PATTERN.match(email) or len(email) > 254:
        raise AuthError("enter a valid email address")
    return email


def validate_password(password: str, email: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if email and password.lower() == normalize_email(email):
        raise AuthError("password must not be the email address")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class AuthStore:
    """Users and sessions in one SQLite file. Safe to share across threads:
    every operation opens its own short-lived connection."""

    def __init__(self, db_path: str | Path, session_hours: int = 12):
        self.db_path = Path(db_path)
        self.session_hours = session_hours
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _user(row) -> User:
        return User(row["id"], row["email"], bool(row["is_active"]), row["created_at"], row["last_login_at"])

    # accounts ---------------------------------------------------------------

    def create_user(self, email: str, password: str) -> User:
        email = validate_email(email)
        validate_password(password, email)
        try:
            with self._connect() as db:
                cur = db.execute(
                    "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (email, hash_password(password), _now().isoformat()),
                )
                row = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        except sqlite3.IntegrityError:
            raise AuthError(f"an account for {email} already exists")
        return self._user(row)

    def get_user(self, email: str) -> User | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
        return self._user(row) if row else None

    def list_users(self) -> list[User]:
        with self._connect() as db:
            return [self._user(r) for r in db.execute("SELECT * FROM users ORDER BY email")]

    def set_password(self, email: str, password: str) -> None:
        validate_password(password, email)
        with self._connect() as db:
            cur = db.execute("UPDATE users SET password_hash = ? WHERE email = ?",
                             (hash_password(password), normalize_email(email)))
            if cur.rowcount == 0:
                raise AuthError(f"no account for {normalize_email(email)}")
            # changing a password signs the user out everywhere
            db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE email = ?)",
                       (normalize_email(email),))

    def set_active(self, email: str, active: bool) -> None:
        with self._connect() as db:
            cur = db.execute("UPDATE users SET is_active = ? WHERE email = ?",
                             (int(active), normalize_email(email)))
            if cur.rowcount == 0:
                raise AuthError(f"no account for {normalize_email(email)}")
            if not active:
                db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE email = ?)",
                           (normalize_email(email),))

    def delete_user(self, email: str) -> None:
        with self._connect() as db:
            cur = db.execute("DELETE FROM users WHERE email = ?", (normalize_email(email),))
            if cur.rowcount == 0:
                raise AuthError(f"no account for {normalize_email(email)}")

    def authenticate(self, email: str, password: str) -> User | None:
        """The active user for these credentials, or None. Constant-ish time either way."""
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
        if row is None:
            verify_password(_DUMMY_HASH, password)
            return None
        if not verify_password(row["password_hash"], password) or not row["is_active"]:
            return None
        if _hasher.check_needs_rehash(row["password_hash"]):
            with self._connect() as db:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
        return self._user(row)

    # sessions ---------------------------------------------------------------

    def create_session(self, user: User) -> tuple[str, Session]:
        """Return (raw cookie token, session). Only the token's hash is stored."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(hours=self.session_hours)
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (_hash_token(token), user.id, csrf, now.isoformat(), expires.isoformat()),
            )
            db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now.isoformat(), user.id))
        return token, Session(user, csrf, expires)

    def get_session(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT s.csrf_token, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?",
                (_hash_token(token),),
            ).fetchone()
            if row is None:
                return None
            expires = datetime.fromisoformat(row["expires_at"])
            if expires <= _now() or not row["is_active"]:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
                return None
        return Session(self._user(row), row["csrf_token"], expires)

    def delete_session(self, token: str | None) -> None:
        if token:
            with self._connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


class LoginThrottle:
    """Lock an email out for a while after repeated failed logins (per process)."""

    def __init__(self, max_failures: int = 5, lockout_minutes: int = 15, clock=time.monotonic):
        self.max_failures = max_failures
        self.window = lockout_minutes * 60
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - self.window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = recent
        return recent

    def retry_after(self, email: str) -> int:
        """Seconds until another attempt is allowed; 0 when not locked."""
        key = normalize_email(email)
        with self._lock:
            recent = self._recent(key)
            if len(recent) < self.max_failures:
                return 0
            return max(1, int(recent[0] + self.window - self._clock()))

    def failure(self, email: str) -> None:
        key = normalize_email(email)
        with self._lock:
            self._recent(key).append(self._clock())

    def success(self, email: str) -> None:
        with self._lock:
            self._failures.pop(normalize_email(email), None)


def csrf_matches(expected: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(expected, provided)
