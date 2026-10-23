"""API settings, read from environment variables (and an optional .env file).

REPORISK_ENV            "production" (default) or "development". Production forces
                        Secure cookies and disables the interactive /docs.
REPORISK_REPOS          Optional: pre-approved local git repositories, separated by the
                        OS path separator (":" on macOS/Linux, ";" on Windows). Clients
                        refer to them by id, never by path. Public GitHub URLs can be
                        analyzed without listing anything here.
REPORISK_DATA_DIR       Where generated datasets and model outputs go
                        (default: output/api, relative to the working directory).
REPORISK_DB_PATH        SQLite file holding user accounts and sessions
                        (default: instance/reporisk.sqlite3).
REPORISK_CORS_ORIGINS   Comma-separated browser origins allowed to call the API.
REPORISK_COOKIE_SECURE  "true"/"false"; only "false" is honoured, and only in development.
REPORISK_SESSION_HOURS  Session lifetime in hours (default 12).
REPORISK_FRONTEND_DIST  Optional path to the built frontend (frontend/dist) to serve
                        from the API, so the app and API share one origin.
REPORISK_CLONE_DIR      Where GitHub repositories are cloned temporarily (default: the
                        system temp directory). Each analysis gets its own folder, which
                        is deleted when the analysis ends.
REPORISK_CLONE_TIMEOUT  Seconds allowed for a clone (default 180).
REPORISK_MAX_REPO_MB    Largest clone accepted, in MB (default 500).
REPORISK_MAX_COMMITS    Most commits accepted in a cloned repository (default 20000);
                        this bounds analysis time.
REPORISK_MAX_QUEUED     Most analyses queued or running at once (default 3).

Values already set in the environment win over the .env file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
DEFAULT_DATA_DIR = "output/api"
DEFAULT_DB_PATH = "instance/reporisk.sqlite3"
ENVIRONMENTS = ("production", "development")


class ConfigError(ValueError):
    """The configuration is invalid or unsafe."""


@dataclass(frozen=True)
class RepositoryConfig:
    id: str
    name: str
    path: Path
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class Settings:
    repositories: tuple[RepositoryConfig, ...] = ()
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    max_workers: int = 1
    environment: str = "development"
    db_path: Path = Path(DEFAULT_DB_PATH)
    cookie_secure: bool = False
    session_hours: int = 12
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    frontend_dist: Path | None = None
    clone_dir: Path | None = None
    clone_timeout_seconds: int = 180
    max_repo_mb: int = 500
    max_commits: int = 20000
    max_queued_analyses: int = 3
    _by_id: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        if self.environment not in ENVIRONMENTS:
            raise ConfigError(f"REPORISK_ENV must be one of {ENVIRONMENTS}, got {self.environment!r}")
        if self.production and not self.cookie_secure:
            raise ConfigError("production requires Secure session cookies (serve the app over HTTPS)")
        if self.session_hours <= 0:
            raise ConfigError("REPORISK_SESSION_HOURS must be positive")
        for name in ("clone_timeout_seconds", "max_repo_mb", "max_commits", "max_queued_analyses"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} must be positive")
        object.__setattr__(self, "_by_id", {r.id: r for r in self.repositories})

    @property
    def production(self) -> bool:
        return self.environment == "production"

    def repository(self, repo_id: str) -> RepositoryConfig | None:
        return self._by_id.get(repo_id)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "repo"


def build_repositories(paths: Iterable[str]) -> tuple[RepositoryConfig, ...]:
    """Turn configured paths into repositories with unique ids, checking each one."""
    repos, seen = [], set()
    for raw in paths:
        raw = raw.strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        base = _slug(path.name)
        repo_id, n = base, 2
        while repo_id in seen:
            repo_id, n = f"{base}-{n}", n + 1
        seen.add(repo_id)
        if not path.is_dir():
            repos.append(RepositoryConfig(repo_id, path.name, path, False, "directory does not exist"))
        elif not (path / ".git").exists():
            repos.append(RepositoryConfig(repo_id, path.name, path, False, "not a git repository"))
        else:
            repos.append(RepositoryConfig(repo_id, path.name, path, True))
    return tuple(repos)


def read_env_file(path: str | Path = ".env") -> dict[str, str]:
    """Parse simple KEY=VALUE lines; blank lines and # comments are ignored."""
    values: dict[str, str] = {}
    path = Path(path)
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip().removeprefix("export ").strip()] = value
    return values


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}")


def settings_from_env(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        env = {**read_env_file(os.environ.get("REPORISK_ENV_FILE", ".env")), **os.environ}
    environment = (env.get("REPORISK_ENV") or "production").strip().lower()
    repos = build_repositories((env.get("REPORISK_REPOS") or "").split(os.pathsep))
    origins = tuple(
        o.strip() for o in (env.get("REPORISK_CORS_ORIGINS") or "").split(",") if o.strip()
    ) or DEFAULT_CORS_ORIGINS
    # Cookies are Secure unless development explicitly turns that off for plain-HTTP localhost.
    cookie_secure = not (
        environment == "development"
        and (env.get("REPORISK_COOKIE_SECURE") or "").strip().lower() == "false"
    )
    dist = env.get("REPORISK_FRONTEND_DIST")
    clone_dir = env.get("REPORISK_CLONE_DIR")
    return Settings(
        repositories=repos,
        data_dir=Path(env.get("REPORISK_DATA_DIR") or DEFAULT_DATA_DIR),
        cors_origins=origins,
        environment=environment,
        db_path=Path(env.get("REPORISK_DB_PATH") or DEFAULT_DB_PATH),
        cookie_secure=cookie_secure,
        session_hours=_int(env, "REPORISK_SESSION_HOURS", 12),
        frontend_dist=Path(dist) if dist else None,
        clone_dir=Path(clone_dir) if clone_dir else None,
        clone_timeout_seconds=_int(env, "REPORISK_CLONE_TIMEOUT", 180),
        max_repo_mb=_int(env, "REPORISK_MAX_REPO_MB", 500),
        max_commits=_int(env, "REPORISK_MAX_COMMITS", 20000),
        max_queued_analyses=_int(env, "REPORISK_MAX_QUEUED", 3),
    )
