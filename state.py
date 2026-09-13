"""Shared state model for the SprintPulse graph.

``PulseState`` is the single source of truth that flows between nodes. Domain
objects are Pydantic models so every LLM step can use structured output and
every deterministic step produces the same shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

    @property
    def rank(self) -> int:
        return ["low", "medium", "high", "critical"].index(self.value)


class Health(str, Enum):
    healthy = "healthy"
    watch = "watch"
    at_risk = "at_risk"
    critical = "critical"


class SprintMeta(BaseModel):
    project_id: str
    project_name: str
    number: int
    name: str
    start_date: str
    end_date: str
    as_of: str
    goal: str = ""


class Task(BaseModel):
    id: str
    title: str
    status: Literal["todo", "in_progress", "blocked", "done"]
    priority: Severity = Severity.medium
    owner: str = ""
    story_points: int = 0
    due_date: Optional[str] = None
    last_update: Optional[str] = None
    blocked_since: Optional[str] = None
    blocker_reason: str = ""
    depends_on: list[str] = Field(default_factory=list)
    notes: str = ""


class Sprint(BaseModel):
    meta: SprintMeta
    team: list[str] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)


class SprintMetrics(BaseModel):
    sprint_number: int
    total_tasks: int
    done: int
    in_progress: int
    todo: int
    blocked: int
    total_points: int
    done_points: int
    blocked_points: int
    completion_pct: float
    points_completion_pct: float
    overdue_tasks: int
    unowned_tasks: int
    days_remaining: int
    health: Health
    health_score: int
    health_reasons: list[str] = Field(default_factory=list)


class Blocker(BaseModel):
    """A blocker found by the detection tool, explicit or inferred."""

    task_id: str
    title: str
    owner: str = ""
    priority: Severity = Severity.medium
    kind: Literal["explicit", "dependency", "stale", "overdue"]
    reason: str
    days_blocked: int = 0
    blocking_points: int = 0
    carried_over: bool = False


class Risk(BaseModel):
    id: str
    title: str
    severity: Severity
    category: Literal["delivery", "dependency", "quality", "resourcing", "compliance", "process"]
    evidence: list[str] = Field(default_factory=list)
    recommendation: str
    requires_escalation: bool = False
    source: Literal["llm", "rules"] = "rules"


class RiskAssessment(BaseModel):
    """Structured output contract for the risk-assessment LLM call."""

    risks: list[Risk] = Field(default_factory=list)


class TrendSignal(BaseModel):
    label: str
    direction: Literal["improving", "declining", "flat"]
    current: str
    previous: str
    commentary: str


class RecurringIssue(BaseModel):
    task_id: str
    title: str
    sprints_seen: list[int] = Field(default_factory=list)
    days_outstanding: int = 0
    detail: str = ""
    source: Literal["data", "memory"] = "data"


class ReportNarrative(BaseModel):
    """Structured output contract for the report-writing LLM call."""

    headline: str
    executive_summary: str
    key_wins: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class WeeklyReport(BaseModel):
    project_name: str
    sprint_name: str
    period: str
    generated_at: str
    health: Health
    headline: str
    executive_summary: str
    progress: dict[str, Any] = Field(default_factory=dict)
    key_wins: list[str] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    trends: list[TrendSignal] = Field(default_factory=list)
    recurring_issues: list[RecurringIssue] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    needs_attention: list[str] = Field(default_factory=list)
    narrative_source: Literal["llm", "template"] = "template"
    degraded_notes: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        from report_render import render_markdown  # local import avoids a cycle

        return render_markdown(self)


class Escalation(BaseModel):
    """One item put in front of a human before it reaches the final report."""

    risk_id: str
    title: str
    severity: Severity
    rationale: str
    proposed_action: str


class ApprovalDecision(BaseModel):
    risk_id: str
    decision: Literal["approve", "reject", "revise"]
    note: str = ""


class MemoryRecord(BaseModel):
    """A memory returned from Mem0 (or the degraded fallback store)."""

    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    memory_id: str = ""


class NodeError(BaseModel):
    node: str
    kind: Literal["data", "llm", "memory", "internal"]
    message: str
    severity: Literal["critical", "degraded"] = "degraded"
    attempts: int = 1


def _extend(left: list, right: list) -> list:
    """Reducer so concurrent/looping nodes append instead of overwrite."""
    return (left or []) + (right or [])


class PulseState(TypedDict, total=False):
    """State shared by every node in the graph."""

    # --- inputs -------------------------------------------------------
    project_id: str
    sprint_number: int
    faults: list[str]

    # --- tool output --------------------------------------------------
    current_sprint: Optional[Sprint]
    previous_sprint: Optional[Sprint]

    # --- analysis -----------------------------------------------------
    metrics: Optional[SprintMetrics]
    previous_metrics: Optional[SprintMetrics]
    blockers: list[Blocker]
    memories: list[MemoryRecord]
    trends: list[TrendSignal]
    recurring_issues: list[RecurringIssue]
    risks: list[Risk]

    # --- human in the loop -------------------------------------------
    escalations: list[Escalation]
    approvals: list[ApprovalDecision]
    human_context: Annotated[list[str], _extend]
    revision_count: int
    revision_requested: bool
    review_required: bool

    # --- output -------------------------------------------------------
    report: Optional[WeeklyReport]
    memory_writes: int

    # --- operational --------------------------------------------------
    errors: Annotated[list[NodeError], _extend]
    degraded: Annotated[list[str], _extend]
    status: Literal["running", "awaiting_review", "completed", "failed"]
    halt_reason: str
