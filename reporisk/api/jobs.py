"""Background analysis jobs, kept in memory.

Analyses run on a small thread pool so HTTP requests return immediately;
clients poll the job status. Results are lost when the server restarts.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import RepositoryConfig
from .engine import STAGES, AnalysisResult, run_analysis

log = logging.getLogger(__name__)
STAGE_LABELS = dict(STAGES)
STAGE_INDEX = {name: i for i, (name, _) in enumerate(STAGES, start=1)}


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
    def __init__(self, data_dir: Path, max_workers: int = 1, runner=run_analysis):
        self._data_dir = Path(data_dir)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reporisk")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._runner = runner

    def submit(self, repository: RepositoryConfig, options) -> Job:
        job = Job(uuid.uuid4().hex[:12], repository, options)
        with self._lock:
            self._jobs[job.id] = job
        job.future = self._executor.submit(self._run, job)
        return job

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
        try:
            result = self._runner(
                str(job.repository.path),
                job.repository.id,
                job.repository.name,
                job.options,
                self._data_dir / job.id,
                lambda stage, message: self._progress(job, stage, message),
            )
        except Exception as exc:  # report any engine failure on the job
            log.exception("analysis %s failed", job.id)
            with self._lock:
                job.status, job.error, job.finished_at = "failed", f"{type(exc).__name__}: {exc}", _now()
            return
        with self._lock:
            job.result, job.status, job.finished_at, job.message = result, "completed", _now(), None
