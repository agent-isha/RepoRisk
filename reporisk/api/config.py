"""API settings, read from environment variables.

REPORISK_REPOS         Permitted local git repositories, separated by the OS path
                       separator (":" on macOS/Linux, ";" on Windows). Only these
                       can be analyzed; clients refer to them by id, never by path.
REPORISK_DATA_DIR      Where generated datasets and model outputs go
                       (default: output/api, relative to the working directory).
REPORISK_CORS_ORIGINS  Comma-separated browser origins allowed to call the API
                       (default: the local Vite dev server).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
DEFAULT_DATA_DIR = "output/api"


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
    _by_id: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_by_id", {r.id: r for r in self.repositories})

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


def settings_from_env(env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    repos = build_repositories((env.get("REPORISK_REPOS") or "").split(os.pathsep))
    origins = tuple(
        o.strip() for o in (env.get("REPORISK_CORS_ORIGINS") or "").split(",") if o.strip()
    ) or DEFAULT_CORS_ORIGINS
    return Settings(
        repositories=repos,
        data_dir=Path(env.get("REPORISK_DATA_DIR") or DEFAULT_DATA_DIR),
        cors_origins=origins,
    )
