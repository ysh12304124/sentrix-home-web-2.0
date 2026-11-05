"""Independent analysis job APIs and offline exports."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from photobench.api.deps import get_container

router = APIRouter(tags=["error-analysis"])


class AnalysisRequest(BaseModel):
    kind: Literal["rules", "compare", "ai"] = "rules"
    parent_job: str | None = None
    left_job: str | None = None
    right_job: str | None = None
    indices: list[int] = Field(default_factory=list, max_length=5000)
    provider_id: str | None = None
    mode: Literal["overview", "cases"] = "cases"
    scope: Literal["run", "filter", "selection"] = "selection"
    filters: dict[str, str] = Field(default_factory=dict)
    max_cases: int = Field(12, ge=1, le=100)
    resume_job: str | None = None
    summary_only: bool = False

    @model_validator(mode="after")
    def validate_options(self) -> AnalysisRequest:
        if self.kind == "compare" and not (self.left_job and self.right_job):
            raise ValueError("left_job and right_job are required")
        if self.kind == "ai" and not (
            self.parent_job and self.provider_id and (self.indices or self.scope != "selection")
        ):
            raise ValueError("parent_job, provider_id and selected indices are required")
        if self.kind == "ai":
            self.filters = {
                k: str(v)
                for k, v in AnalysisQuery.model_validate(self.filters).conditions().items()
                if v != ""
            }
            if self.summary_only and not self.resume_job:
                raise ValueError("summary_only requires resume_job")
        return self


class AnomalyEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str
    code: str
    label: str
    source: str
    turn: int
    step_id: str | None = None
    recovery: Literal["recovered", "not_recovered", "unknown"]
    basis: str
    role: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    tool_name: str | None = None
    component: str | None = None
    issue_kind: str | None = None
    failure_reason: str | None = None
    tool_call_id: str | None = None
    parent_step_id: str | None = None
    model_call_id: str | None = None
    call_key: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    related_event_ids: list[str] = Field(default_factory=list)


class CaseReference(BaseModel):
    model_config = ConfigDict(extra="allow")
    index: int
    qa_id: str
    question: str
    answer: str
    reference_answer: str
    score: int | None
    execution: str
    dimensions: dict[str, str]
    events: list[AnomalyEvent]
    turns: list[dict[str, Any]]
    missing: list[str]
    signals: list[str]


class BehaviorGroup(BaseModel):
    value: str
    total: int
    valid: int
    correct: int
    incorrect: int
    missing: int


class GroupPartition(BaseModel):
    total: int
    labeled: int = 0
    unknown: int = 0
    not_applicable: int = 0
    multi_labeled: int = 0
    exclusive: bool
    exhaustive: bool


class GroupError(BaseModel):
    unit: Literal["qa", "turn"]
    dimension: str
    value: str
    code: str
    count: int


class AnalysisReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int
    rule_version: int
    generated_at: float
    source: dict[str, Any]
    result_fingerprint: str
    gt_fingerprint: str
    ability_taxonomy_versions: list[str] = Field(default_factory=list)
    ability_labels: dict[str, str] = Field(default_factory=dict)
    annotation_fingerprint: str | None = None
    group_partitions: dict[str, dict[str, GroupPartition]] = Field(default_factory=dict)
    group_errors: list[GroupError] = Field(default_factory=list)
    turn_groups: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    item_count: int
    counts: dict[str, int]
    denominators: dict[str, int]
    groups: dict[str, list[dict[str, Any]]]
    errors: list[dict[str, Any]]
    behavior_groups: list[BehaviorGroup] = Field(default_factory=list)
    behavior_scenarios: list[BehaviorGroup] = Field(default_factory=list)
    behavior_matrix: list[dict[str, Any]]
    heatmap: list[dict[str, Any]]
    ability_heatmap: list[dict[str, Any]] = Field(default_factory=list)
    definitions: list[str]


class ComparisonReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    comparable: bool
    sources: dict[str, Any]
    differences: list[str]
    missing_configuration: list[str]
    duplicates: list[str]
    pairing_counts: dict[str, int]
    quality_changes: dict[str, int] | None
    rows: list[dict[str, Any]]
    note: str


class DiagnosisReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    parent_job: str
    model: str
    provider_id: str
    requested: int
    covered: int
    cases: list[dict[str, Any]]
    note: str
    schema_version: int = 1
    scope: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, int] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    overview: dict[str, Any] | None = None
    requests: list[dict[str, Any]] = Field(default_factory=list)
    stage: str = "cases"
    prompt_version: str | None = None
    configuration_fingerprint: str | None = None
    sample_indices: list[int] = Field(default_factory=list)
    summary_status: str | None = None
    summary_error: str | None = None


class EvidenceSnapshot(BaseModel):
    evidence_locations: dict[str, str]
    job_id: str
    index: int
    result_fingerprint: str
    item: dict[str, Any]


class CasePage(BaseModel):
    items: list[CaseReference]
    total: int
    page: int
    page_size: int


class AnalysisJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    job_id: str | None = None
    run_id: str | None = None
    kind: str | None = None
    processed: int = 0
    stale: bool = False
    report: AnalysisReport | ComparisonReport | DiagnosisReport | None = None


class AnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    format: Literal["html", "json", "csv"] = "html"
    score: Literal["", "0", "1", "2", "None", "low"] = ""
    recovery: Literal["", "recovered", "not_recovered", "unknown"] = ""
    anomaly: Literal["", "yes", "no"] = ""
    content_unit: Literal["", "qa", "turn"] = ""
    behavior_result: Literal["", "correct", "incorrect", "missing"] = ""
    turn: int | None = Field(None, ge=0)
    index: int | None = Field(None, ge=0)
    q: str = Field("", max_length=500)
    code: str = ""
    question_type: str = ""
    expected_action: str = ""
    actual_action: str = ""
    execution: Literal["", "normal", "failed", "cancelled", "unknown"] = ""
    task_type: str = ""
    angle: str = ""
    difficulty: str = ""
    answerability: str = ""
    media_type: str = ""
    conversation_type: str = ""
    ability_family: str = ""
    ability_type: str = ""
    ability_taxonomy_version: str = ""
    benchmark_group: str = ""
    content_type: str = ""
    content_subtype: str = ""
    test_scenario: str = ""
    tool_name: str = ""
    issue_kind: str = ""
    failure_reason: str = ""
    component: str = ""
    signal: str = ""

    def conditions(self) -> dict[str, Any]:
        return self.model_dump(exclude={"page", "page_size", "format"}, exclude_none=True)


@router.post(
    "/runs/{run_id}/analysis", status_code=202, response_model=AnalysisJob, response_model_exclude_none=True
)
def create(request: Request, run_id: str, payload: AnalysisRequest) -> dict[str, Any]:
    return get_container(request).error_analysis.create(run_id, payload.model_dump(exclude_none=True))


@router.get("/runs/{run_id}/analysis", response_model=AnalysisJob, response_model_exclude_none=True)
def latest(request: Request, run_id: str) -> dict[str, Any]:
    return get_container(request).analysis_backend.latest(run_id)


@router.get("/runs/{run_id}/analyses", response_model=list[AnalysisJob], response_model_exclude_none=True)
def history(request: Request, run_id: str) -> list[dict[str, Any]]:
    return get_container(request).analysis_backend.history(run_id)


@router.get("/analyses/{job_id}", response_model=AnalysisJob, response_model_exclude_none=True)
def job(request: Request, job_id: str) -> dict[str, Any]:
    return get_container(request).analysis_backend.result(job_id)


@router.post("/analyses/{job_id}/cancel", response_model=AnalysisJob, response_model_exclude_none=True)
def cancel(request: Request, job_id: str) -> dict[str, Any]:
    return get_container(request).analysis_backend.cancel(job_id)


@router.get("/analyses/{job_id}/cases", response_model=CasePage)
def cases(request: Request, job_id: str, query: Annotated[AnalysisQuery, Query()]) -> dict[str, Any]:
    return get_container(request).analysis_backend.cases(
        job_id, query.conditions(), query.page, query.page_size
    )


class IssueGroup(BaseModel):
    component: str
    group_id: str
    tool_name: str
    code: str
    label: str
    failure_reason: str
    issue_kind: str
    affected_items: int
    affected_calls: int
    low_score_items: int
    indices: list[int]
    event_ids: list[str]
    events: int
    recovery: dict[str, int]


class ToolSummary(BaseModel):
    tool_name: str
    recorded_calls: int
    using_items: int
    affected_items: int
    affected_calls: int


class AnalysisFacet(BaseModel):
    value: str
    count: int


class AnalysisView(CasePage):
    summary: AnalysisReport
    issues: list[IssueGroup]
    tools: list[ToolSummary]
    facets: dict[str, list[AnalysisFacet]]
    ability_tree: dict[str, list[str]]


@router.get("/analyses/{job_id}/view", response_model=AnalysisView)
def view(request: Request, job_id: str, query: Annotated[AnalysisQuery, Query()]) -> dict[str, Any]:
    return get_container(request).analysis_backend.view(
        job_id, query.conditions(), query.page, query.page_size
    )


@router.get("/analyses/{job_id}/evidence/{index}", response_model=EvidenceSnapshot)
def evidence(request: Request, job_id: str, index: int) -> dict[str, Any]:
    return get_container(request).analysis_backend.evidence(job_id, index)


@router.get("/analyses/{job_id}/export")
def export(request: Request, job_id: str, query: Annotated[AnalysisQuery, Query()]) -> Response:
    body = get_container(request).analysis_backend.export(job_id, query.format, query.conditions())
    media = {"html": "text/html", "json": "application/json", "csv": "text/csv"}[query.format]
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{job_id}-analysis.{query.format}"'},
    )
