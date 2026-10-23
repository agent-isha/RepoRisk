"""Background analysis jobs, kept in memory.

Analyses run on a small thread pool so HTTP requests return immediately;
clients poll the job status. Results are lost when the server restarts.

GitHub jobs are cloned on the worker thread into their own temporary folder,
which is deleted when the job ends, whether it succeeds or fails.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import RepositoryConfig
from .engine import STAGES, AnalysisResult, run_analysis
from .github import CloneError, GitHubRepo, check_limits, clone_repository, remove_tree

log = logging.getLogger(__name__)
STAGE_LABELS = {"cloning": "Cloning the repository from GitHub", **dict(STAGES)}
STAGE_INDEX = {name: i for i, (name, _) in enumerate(STAGES, start=1)}
ACTIVE_STATES = ("queued", "running")


class QueueFull(RuntimeError):
    """Too many analyses are already queued or running."""


@dataclass(frozen=True)
class CloneLimits:
    clone_dir: Path | None = None
    timeout_seconds: int = 180
    max_repo_mb: int = 500
    max_commits: int = 20000


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    id: str
    repository: RepositoryConfig
    options: object
    status: str = "queued"
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: AnalysisResult | None = None
    future: Future | None = None
    github: GitHubRepo | None = None

    def status_dict(self) -> dict:
        return {
            "id": self.id,
            "repository_id": self.repository.id,
            "repository_name": self.repository.name,
            "status": self.status,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage),
            "stage_index": STAGE_INDEX.get(self.stage, len(STAGES) if self.status == "completed" else 0),
            "stage_count": len(STAGES),
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "options": self.options,
        }


class JobManager:
    def __init__(self, data_dir: Path, max_workers: int = 1, runner=run_analysis,
                 max_queued: int | None = None, clone_limits: CloneLimits = CloneLimits(),
                 cloner=clone_repository):
        self._data_dir = Path(data_dir)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reporisk")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._runner = runner
        self._max_queued = max_queued
        self._clone_limits = clone_limits
        self._cloner = cloner

    def submit(self, repository: RepositoryConfig, options, github: GitHubRepo | None = None) -> Job:
        job = Job(uuid.uuid4().hex[:12], repository, options, github=github)
        with self._lock:
            active = sum(1 for j in self._jobs.values() if j.status in ACTIVE_STATES)
            if self._max_queued is not None and active >= self._max_queued:
                raise QueueFull(f"{active} analyses are already queued or running. Try again when one finishes.")
            self._jobs[job.id] = job
        job.future = self._executor.submit(self._run, job)
        return job

    def submit_github(self, repo: GitHubRepo, options) -> Job:
        # The path is filled in by the worker once the clone exists.
        source = RepositoryConfig(f"github:{repo.full_name}", repo.full_name, Path(), True)
        return self.submit(source, options, github=repo)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        job = self.get(job_id)
        if job and job.future:
            job.future.result(timeout=timeout)
        return job

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _progress(self, job: Job, stage: str, message: str) -> None:
        with self._lock:
            if stage in STAGE_LABELS:
                job.stage = stage
            job.message = message or None

    def _run(self, job: Job) -> None:
        with self._lock:
            job.status, job.started_at = "running", _now()
        clone_root = None
        repo_path = job.repository.path
        try:
            if job.github is not None:
                limits = self._clone_limits
                if limits.clone_dir is not None:
                    Path(limits.clone_dir).mkdir(parents=True, exist_ok=True)
                clone_root = Path(tempfile.mkdtemp(prefix="reporisk-clone-", dir=limits.clone_dir))
                repo_path = clone_root / "repo"
                self._progress(job, "cloning", f"Cloning {job.github.full_name} from GitHub")
                self._cloner(job.github, repo_path, limits.timeout_seconds)
                check_limits(job.github, repo_path, limits.max_repo_mb, limits.max_commits)
            result = self._runner(
                str(repo_path),
                job.repository.id,
                job.repository.name,
                job.options,
                self._data_dir / job.id,
                lambda stage, message: self._progress(job, stage, message),
            )
        except CloneError as exc:  # already a user-safe message
            self._fail(job, str(exc))
            return
        except Exception as exc:  # report any engine failure on the job, without server paths
            log.exception("analysis %s failed", job.id)
            self._fail(job, _safe_error(exc, [repo_path, clone_root, self._data_dir]))
            return
        finally:
            if clone_root is not None:
                remove_tree(clone_root)
        with self._lock:
            job.result, job.status, job.finished_at, job.message = result, "completed", _now(), None

    def _fail(self, job: Job, message: str) -> None:
        with self._lock:
            job.status, job.error, job.finished_at, job.message = "failed", message, _now(), None


def _safe_error(exc: Exception, paths) -> str:
    """The exception as text, with server filesystem paths replaced."""
    message = f"{type(exc).__name__}: {exc}"
    for path in paths:
        if path is None or str(path) in ("", "."):
            continue
        for variant in {str(path), str(Path(path).resolve())}:
            message = message.replace(variant, "<server path>")
    return message[:500]
