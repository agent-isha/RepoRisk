"""FastAPI application wrapping the RepoRisk engine.

Run locally (see DEVELOPMENT.md; settings come from the environment or .env):
    python -m uvicorn reporisk.api.main:app --host 127.0.0.1 --port 8000

This module only defines create_app(); reporisk/api/main.py builds the app from
the environment, so importing this module has no side effects.

Every /api/* route except POST /api/auth/login requires a session cookie, and
state-changing requests also require the session's CSRF token.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..model import LogisticRegression, sklearn
from . import schemas
from .auth import CSRF_HEADER, SESSION_COOKIE, AuthError, AuthStore, LoginThrottle, Session, csrf_matches
from .config import ConfigError, Settings, settings_from_env
from .engine import BUG_FIX_LIMITATIONS
from .github import parse_github_url
from .jobs import CloneLimits, Job, JobManager, QueueFull

API_VERSION = "0.2.0"
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
    401: {"model": schemas.ErrorResponse},
    403: {"model": schemas.ErrorResponse},
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
    429: {"model": schemas.ErrorResponse},
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    ),
}
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")  # Swagger UI loads assets from a CDN


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None, headers=None):
        self.status, self.code, self.message, self.details = status, code, message, details
        self.headers = headers


def _error(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(status_code=status, content=body, headers=headers)


# uvicorn configures this logger, so these lines appear in its console output.
startup_log = logging.getLogger("uvicorn.error")


def startup_summary(settings: Settings, store: AuthStore) -> list:
    """Human-readable startup lines; problems that block sign-in or analysis start with WARNING."""
    available = sum(1 for r in settings.repositories if r.available)
    lines = [
        f"RepoRisk API {API_VERSION}: environment={settings.environment}, "
        f"docs={'disabled' if settings.production else '/docs'}, "
        f"session cookies {'Secure (HTTPS only)' if settings.cookie_secure else 'not Secure (local HTTP)'}, "
        f"configured repositories {available} of {len(settings.repositories)} available, "
        f"public GitHub URLs accepted, accounts db={settings.db_path}",
    ]
    if settings.production and not os.environ.get("REPORISK_ENV"):
        lines.append("WARNING: REPORISK_ENV is not set, so production mode is used. For local development "
                     "create .env from .env.example (REPORISK_ENV=development).")
    if not store.list_users():
        lines.append("WARNING: no accounts exist, so nobody can sign in. Create one with: "
                     "python -m reporisk.api.users create you@example.com")
    for r in settings.repositories:
        if not r.available:
            lines.append(f"WARNING: repository {r.id!r} is unavailable: {r.reason}")
    return lines


def _user_out(session: Session) -> dict:
    user = session.user
    return {
        "user": {"email": user.email, "created_at": user.created_at, "last_login_at": user.last_login_at},
        "csrf_token": session.csrf_token,
        "expires_at": session.expires_at,
    }


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or settings_from_env()
    jobs = JobManager(
        settings.data_dir, settings.max_workers, max_queued=settings.max_queued_analyses,
        clone_limits=CloneLimits(settings.clone_dir, settings.clone_timeout_seconds,
                                 settings.max_repo_mb, settings.max_commits),
    )
    store = AuthStore(settings.db_path, settings.session_hours)
    provisioned = None
    if settings.admin_email:
        try:
            provisioned = store.ensure_user(settings.admin_email, settings.admin_password)
        except AuthError as exc:  # e.g. the password is too short; never log the password
            raise ConfigError(f"REPORISK_ADMIN_EMAIL/REPORISK_ADMIN_PASSWORD: {exc}")
    throttle = LoginThrottle(settings.login_max_failures, settings.login_lockout_minutes)
    allowed_origins = set(settings.cors_origins)

    @asynccontextmanager
    async def lifespan(_app):
        if provisioned:
            startup_log.info(f"Account from REPORISK_ADMIN_EMAIL ({settings.admin_email}): {provisioned}")
        for line in startup_summary(settings, store):
            if line.startswith("WARNING: "):
                startup_log.warning(line[len("WARNING: "):])
            else:
                startup_log.info(line)
        yield
        jobs.shutdown()

    docs = {} if not settings.production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="RepoRisk API", version=API_VERSION, lifespan=lifespan, **docs)
    app.state.settings, app.state.jobs, app.state.auth = settings, jobs, store
    app.state.login_throttle = throttle
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", CSRF_HEADER],
    )

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # Refuse state-changing requests from foreign origins (defence in depth with
        # SameSite cookies and the CSRF token). Same-origin requests are allowed.
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin:
            own = f"{request.url.scheme}://{request.url.netloc}"
            if origin != own and origin not in allowed_origins:
                return _error(403, "origin_not_allowed", "Requests from this origin are not allowed.")
        response = await call_next(request)
        if not request.url.path.startswith(DOCS_PATHS):
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return _error(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"location": ".".join(str(p) for p in e["loc"]), "message": e["msg"].removeprefix("Value error, ")}
            for e in exc.errors()
        ]
        return _error(422, "validation_error", "The request is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _exc: Exception):
        return _error(500, "internal_error", "An unexpected server error occurred.")

    # -- authentication ------------------------------------------------------

    def current_session(request: Request) -> Session:
        session = store.get_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise APIError(401, "not_authenticated", "Sign in to continue.")
        return session

    def csrf_protected(request: Request, session: Session = Depends(current_session)) -> Session:
        if not csrf_matches(session.csrf_token, request.headers.get(CSRF_HEADER)):
            raise APIError(403, "csrf_failed", "Missing or invalid CSRF token. Reload the page and try again.")
        return session

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE, token,
            max_age=settings.session_hours * 3600,
            httponly=True, secure=settings.cookie_secure, samesite="strict", path="/",
        )

    @app.post("/api/auth/login", response_model=schemas.SessionInfo, responses=ERROR_RESPONSES,
              tags=["auth"])
    def login(body: schemas.LoginRequest, response: Response):
        retry = throttle.retry_after(body.email)
        if retry:
            raise APIError(429, "too_many_attempts",
                           f"Too many failed sign-in attempts. Try again in {max(1, retry // 60)} minute(s).",
                           headers={"Retry-After": str(retry)})
        user = store.authenticate(body.email, body.password)
        if user is None:
            throttle.failure(body.email)
            raise APIError(401, "invalid_credentials", "Incorrect email or password.")
        throttle.success(body.email)
        token, session = store.create_session(user)
        set_session_cookie(response, token)
        return _user_out(session)

    @app.post("/api/auth/logout", response_model=schemas.LogoutResponse, responses=ERROR_RESPONSES,
              tags=["auth"])
    def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE)
        session = store.get_session(token)
        if session is not None and not csrf_matches(session.csrf_token, request.headers.get(CSRF_HEADER)):
            raise APIError(403, "csrf_failed", "Missing or invalid CSRF token. Reload the page and try again.")
        store.delete_session(token)
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                               secure=settings.cookie_secure, samesite="strict")
        return {"status": "logged_out"}

    @app.get("/api/auth/me", response_model=schemas.SessionInfo, responses=ERROR_RESPONSES, tags=["auth"])
    def me(session: Session = Depends(current_session)):
        return _user_out(session)

    # -- public --------------------------------------------------------------

    if settings.production:
        # The docs are off in production; say so instead of a bare "Not Found".
        def docs_disabled():
            raise APIError(404, "docs_disabled",
                           "Interactive API docs are disabled in production. "
                           "Set REPORISK_ENV=development (for local use) to enable /docs and /openapi.json.")

        for path in DOCS_PATHS:
            app.add_api_route(path, docs_disabled, methods=["GET"], include_in_schema=False)

    @app.get("/health", response_model=schemas.Health)
    def health():
        return {
            "status": "ok",
            "version": API_VERSION,
            "ml_available": LogisticRegression is not None,
            "scikit_learn_version": getattr(sklearn, "__version__", None),
            "repositories_configured": len(settings.repositories),
        }

    # -- analysis (signed-in users only) -------------------------------------

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

    signed_in = [Depends(current_session)]

    @app.get("/api/repositories", response_model=List[schemas.Repository], dependencies=signed_in,
             responses=ERROR_RESPONSES)
    def repositories():
        return [
            {"id": r.id, "name": r.name, "path": str(r.path), "available": r.available, "reason": r.reason}
            for r in settings.repositories
        ]

    @app.post("/api/analyze", response_model=schemas.AnalysisStatus, status_code=202,
              dependencies=[Depends(csrf_protected)], responses=ERROR_RESPONSES)
    def analyze(request: schemas.AnalyzeRequest):
        try:
            if request.github_url is not None:
                # Already validated by the schema; cloning happens on the worker thread.
                return jobs.submit_github(parse_github_url(request.github_url), request.options).status_dict()
            repo = settings.repository(request.repository_id)
            if repo is None:
                raise APIError(404, "repository_not_permitted",
                               f"Repository {request.repository_id!r} is not in the permitted list "
                               "(configure REPORISK_REPOS).")
            if not repo.available:
                raise APIError(409, "repository_unavailable",
                               f"Repository {repo.id!r} cannot be analyzed: {repo.reason}.")
            return jobs.submit(repo, request.options).status_dict()
        except QueueFull as exc:
            raise APIError(429, "too_many_analyses", str(exc))

    @app.get("/api/analyses", response_model=List[schemas.AnalysisStatus], dependencies=signed_in,
             responses=ERROR_RESPONSES)
    def analyses():
        return [j.status_dict() for j in jobs.list()]

    @app.get("/api/analysis/{analysis_id}", response_model=schemas.AnalysisStatus, dependencies=signed_in,
             responses=ERROR_RESPONSES)
    def analysis_status(analysis_id: str):
        job = jobs.get(analysis_id)
        if job is None:
            raise APIError(404, "analysis_not_found", f"No analysis with id {analysis_id!r}.")
        return job.status_dict()

    @app.get("/api/analysis/{analysis_id}/summary", response_model=schemas.Summary, dependencies=signed_in,
             responses=ERROR_RESPONSES)
    def summary(analysis_id: str):
        return {"analysis_id": analysis_id, **completed(analysis_id).result.summary}

    @app.get("/api/analysis/{analysis_id}/files", response_model=schemas.FilesPage, dependencies=signed_in,
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
             dependencies=signed_in, responses=ERROR_RESPONSES)
    def predictions(analysis_id: str, limit: int = Query(100, ge=1, le=5000)):
        data = dict(completed(analysis_id).result.predictions)
        data["predictions"] = data.get("predictions", [])[:limit]
        return {"analysis_id": analysis_id, **data}

    @app.get("/api/analysis/{analysis_id}/bug-fixes", response_model=schemas.BugFixesPage,
             dependencies=signed_in, responses=ERROR_RESPONSES)
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

    # Serve the built frontend from the same origin when configured (production).
    if settings.frontend_dist is not None:
        if not (settings.frontend_dist / "index.html").is_file():
            raise RuntimeError(f"REPORISK_FRONTEND_DIST has no index.html: {settings.frontend_dist}")
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")

    return app

