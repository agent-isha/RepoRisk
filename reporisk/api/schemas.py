"""Request and response schemas.

Annotations use typing.Optional/List because Pydantic evaluates them at
runtime and Python 3.9 doesn't support `X | None`.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AnalysisState = Literal["queued", "running", "completed", "failed"]
RiskLevel = Literal["high", "medium", "low"]
BugFixOutcome = Literal["likely_bug_fix", "uncertain", "not_likely_bug_fix"]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    ml_available: bool
    scikit_learn_version: Optional[str]
    repositories_configured: int


class Repository(BaseModel):
    id: str
    name: str
    path: str
    available: bool
    reason: Optional[str] = None


class AnalysisOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exclude_tests: bool = Field(True, description="leave test files out of rankings, dataset and model")
    source_only: bool = Field(True, description="only analyze source-code files (.py, .js, .java, ...)")
    horizon_days: int = Field(90, ge=7, le=730, description="future window for the ML label")
    snapshot_interval_days: int = Field(90, ge=7, le=365, description="days between dataset snapshots")
    run_szz: bool = Field(True, description="trace likely bug fixes back to candidate inducing commits")
    train_model: bool = Field(True, description="build the dataset and train/evaluate the ML model")


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str = Field(..., min_length=1, max_length=100)
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


class AnalysisStatus(BaseModel):
    id: str
    repository_id: str
    repository_name: str
    status: AnalysisState
    stage: Optional[str] = None
    stage_label: Optional[str] = None
    stage_index: int = 0
    stage_count: int
    message: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    options: AnalysisOptions


class RiskFactor(BaseModel):
    code: str
    label: str
    detail: str


class Definition(BaseModel):
    code: str
    label: str
    rule: str


class FileRisk(BaseModel):
    path: str
    risk_level: RiskLevel
    risk_factors: List[RiskFactor]
    churn: int
    lines_added: int
    lines_deleted: int
    commits: int
    authors: int
    author_hhi: Optional[float]
    churn_30d: int
    churn_90d: int
    churn_180d: int
    coupled_files: int
    strongest_coupling: float
    top_partner: Optional[str]
    bug_fix_commits: int
    first_seen: Optional[datetime]
    last_changed: Optional[datetime]


class FilesPage(BaseModel):
    analysis_id: str
    total: int
    offset: int
    limit: int
    items: List[FileRisk]


class RepositoryInfo(BaseModel):
    id: str
    name: str
    head_commit: Optional[str]


class SzzSummary(BaseModel):
    status: Literal["completed", "skipped"]
    reason: Optional[str] = None
    fixes_traced: int = 0
    fixes_with_candidates: int = 0
    inducing_commits: int = 0
    records: int = 0


class ModelSummary(BaseModel):
    status: Literal["available", "unavailable"]
    reason: Optional[str] = None
    scored_snapshot: Optional[date] = None


class DatasetSummary(BaseModel):
    status: Literal["built", "skipped"]
    reason: Optional[str] = None
    rows: int = 0
    labelled_rows: int = 0
    positives: int = 0
    snapshots_complete: int = 0
    snapshots_incomplete: int = 0


class Summary(BaseModel):
    analysis_id: str
    repository: RepositoryInfo
    completed_at: datetime
    observation_start: Optional[datetime]
    observation_end: Optional[datetime]
    commits_analyzed: int
    files_analyzed: int
    files_touched_total: int
    risk_counts: Dict[str, int]
    risk_level_definitions: List[Definition]
    risk_factor_definitions: List[Definition]
    bug_fix_counts: Dict[str, int]
    szz: SzzSummary
    dataset: DatasetSummary
    model: ModelSummary
    timings_seconds: Dict[str, float]
    warnings: List[str]


class Prediction(BaseModel):
    rank: int
    path: str
    probability: float
    heuristic_risk_level: Optional[RiskLevel]


class EvaluatedModel(BaseModel):
    name: str
    status: Literal["trained", "skipped"]
    reason: Optional[str] = None
    description: Optional[str] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    pr_auc: Optional[float] = None
    roc_auc: Optional[float] = None
    flagged: Optional[int] = None
    precision_at_k: Dict[str, Optional[float]] = Field(default_factory=dict)
    recall_at_k: Dict[str, Optional[float]] = Field(default_factory=dict)


class Evaluation(BaseModel):
    method: str
    test_start: date
    train_rows: int
    train_positives: int
    test_rows: int
    test_positives: int
    test_prevalence: Optional[float]
    purged_train_rows: int
    models: List[EvaluatedModel]
    warnings: List[str]


class PredictionsResponse(BaseModel):
    analysis_id: str
    status: Literal["available", "unavailable"]
    reason: Optional[str] = None
    scored_snapshot: Optional[date] = None
    trained_on_rows: Optional[int] = None
    trained_on_positives: Optional[int] = None
    horizon_days: Optional[int] = None
    evaluation: Optional[Evaluation] = None
    evaluation_unavailable_reason: Optional[str] = None
    predictions: List[Prediction]
    label_note: str
    limitations: List[str]


class LineEvidence(BaseModel):
    fix_parent_line: int
    inducing_line: int
    content: str


class SzzRecord(BaseModel):
    inducing_hash: str
    inducing_date: Optional[datetime]
    path: str
    lines: List[LineEvidence]
    evidence: List[str]
    limitations: List[str]


class ChangedFile(BaseModel):
    path: str
    added: int
    deleted: int


class BugFixCandidate(BaseModel):
    hash: str
    subject: str
    message: str
    timestamp: datetime
    outcome: BugFixOutcome
    evidence: List[str]
    files: List[ChangedFile]
    added: int
    deleted: int
    szz_status: Literal["traced", "not_traced", "skipped"]
    szz_note: Optional[str] = None
    szz_records: List[SzzRecord]


class BugFixesPage(BaseModel):
    analysis_id: str
    total: int
    offset: int
    limit: int
    outcome_counts: Dict[str, int]
    szz: SzzSummary
    items: List[BugFixCandidate]
    limitations: List[str]
