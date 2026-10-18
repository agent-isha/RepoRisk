"""FastAPI application wrapping the RepoRisk engine.

Run locally:
    REPORISK_REPOS=/path/to/repo python -m uvicorn reporisk.api.app:app --port 8000
"""

from contextlib import asynccontextmanager
from typing import List, Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..model import LogisticRegression, sklearn
from . import schemas
from .config import Settings, settings_from_env
from .engine import BUG_FIX_LIMITATIONS
from .jobs import Job, JobManager

API_VERSION = "0.1.0"
FILE_SORT_KEYS = {
    "risk": lambda f: (len(f["risk_factors"]), f["churn"]),
    "path": lambda f: f["path"],
    "churn": lambda f: f["churn"],
    "commits": lambda f: f["commits"],
    "authors": lambda f: f["authors"],
    "churn_90d": lambda f: f["churn_90d"],
    "coupled_files": lambda f: f["coupled_files"],
    "strongest_coupling": lambda f: f["strongest_coupling"],
    "bug_fix_commits": lambda f: f["bug_fix_commits"],
    "last_changed": lambda f: f["last_changed"] or "",
}
FileSort = Literal[
    "risk", "path", "churn", "commits", "authors", "churn_90d",
    "coupled_files", "strongest_coupling", "bug_fix_commits", "last_changed",
]
ERROR_RESPONSES = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(status_code=status, content=body)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or settings_from_env()
    jobs = JobManager(settings.data_dir, settings.max_workers)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.shutdown()

    app = FastAPI(title="RepoRisk API", version=API_VERSION, lifespan=lifespan)
    app.state.settings, app.state.jobs = settings, jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return _error(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"location": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        return _error(422, "validation_error", "The request is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _exc: Exception):
        return _error(500, "internal_error", "An unexpected server error occurred.")

    def completed(analysis_id: str) -> Job:
        job = jobs.get(analysis_id)
        if job is None:
            raise APIError(404, "analysis_not_found", f"No analysis with id {analysis_id!r}.")
        if job.status == "failed":
            raise APIError(409, "analysis_failed", f"The analysis failed: {job.error}")
        if job.status != "completed":
            raise APIError(409, "analysis_not_ready",
                           f"The analysis is {job.status}; results are not available yet.",
                           {"status": job.status, "stage": job.stage})
        return job

    @app.get("/health", response_model=schemas.Health)
    def health():
        return {
            "status": "ok",
            "version": API_VERSION,
            "ml_available": LogisticRegression is not None,
            "scikit_learn_version": getattr(sklearn, "__version__", None),
            "repositories_configured": len(settings.repositories),
        }

    @app.get("/api/repositories", response_model=List[schemas.Repository])
    def repositories():
        return [
            {"id": r.id, "name": r.name, "path": str(r.path), "available": r.available, "reason": r.reason}
            for r in settings.repositories
        ]

    @app.post("/api/analyze", response_model=schemas.AnalysisStatus, status_code=202,
              responses=ERROR_RESPONSES)
    def analyze(request: schemas.AnalyzeRequest):
        repo = settings.repository(request.repository_id)
        if repo is None:
            raise APIError(404, "repository_not_permitted",
                           f"Repository {request.repository_id!r} is not in the permitted list "
                           "(configure REPORISK_REPOS).")
        if not repo.available:
            raise APIError(409, "repository_unavailable",
                           f"Repository {repo.id!r} cannot be analyzed: {repo.reason}.")
        return jobs.submit(repo, request.options).status_dict()

    @app.get("/api/analyses", response_model=List[schemas.AnalysisStatus])
    def analyses():
        return [j.status_dict() for j in jobs.list()]

    @app.get("/api/analysis/{analysis_id}", response_model=schemas.AnalysisStatus,
             responses=ERROR_RESPONSES)
    def analysis_status(analysis_id: str):
        job = jobs.get(analysis_id)
        if job is None:
            raise APIError(404, "analysis_not_found", f"No analysis with id {analysis_id!r}.")
        return job.status_dict()

    @app.get("/api/analysis/{analysis_id}/summary", response_model=schemas.Summary,
             responses=ERROR_RESPONSES)
    def summary(analysis_id: str):
        return {"analysis_id": analysis_id, **completed(analysis_id).result.summary}

    @app.get("/api/analysis/{analysis_id}/files", response_model=schemas.FilesPage,
             responses=ERROR_RESPONSES)
    def files(
        analysis_id: str,
        search: Optional[str] = Query(None, max_length=200, description="case-insensitive path filter"),
        risk_level: Optional[schemas.RiskLevel] = None,
        sort: FileSort = "risk",
        order: Literal["asc", "desc"] = "desc",
        limit: int = Query(100, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ):
        items = completed(analysis_id).result.files
        if search:
            needle = search.lower()
            items = [f for f in items if needle in f["path"].lower()]
        if risk_level:
            items = [f for f in items if f["risk_level"] == risk_level]
        items = sorted(items, key=FILE_SORT_KEYS[sort], reverse=order == "desc")
        return {"analysis_id": analysis_id, "total": len(items), "offset": offset, "limit": limit,
                "items": items[offset:offset + limit]}

    @app.get("/api/analysis/{analysis_id}/predictions", response_model=schemas.PredictionsResponse,
             responses=ERROR_RESPONSES)
    def predictions(analysis_id: str, limit: int = Query(100, ge=1, le=5000)):
        data = dict(completed(analysis_id).result.predictions)
        data["predictions"] = data.get("predictions", [])[:limit]
        return {"analysis_id": analysis_id, **data}

    @app.get("/api/analysis/{analysis_id}/bug-fixes", response_model=schemas.BugFixesPage,
             responses=ERROR_RESPONSES)
    def bug_fixes(
        analysis_id: str,
        outcome: Optional[Literal["likely_bug_fix", "uncertain"]] = None,
        search: Optional[str] = Query(None, max_length=200, description="filter by subject or hash"),
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        result = completed(analysis_id).result
        items = result.bug_fixes
        if outcome:
            items = [b for b in items if b["outcome"] == outcome]
        if search:
            needle = search.lower()
            items = [b for b in items if needle in b["subject"].lower() or b["hash"].startswith(needle)]
        return {
            "analysis_id": analysis_id,
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "outcome_counts": result.summary["bug_fix_counts"],
            "szz": result.summary["szz"],
            "items": items[offset:offset + limit],
            "limitations": BUG_FIX_LIMITATIONS,
        }

    return app


app = create_app()
