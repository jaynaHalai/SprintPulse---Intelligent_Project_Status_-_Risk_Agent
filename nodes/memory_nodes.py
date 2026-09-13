"""Nodes 4 and 9 - read from and write to persistent project memory (Mem0)."""

from __future__ import annotations

from analytics import parse_date
from nodes.common import error, memory_for, settings_for
from state import Blocker, PulseState, Risk
from tools.memory import MemoryEntry

RECALL_QUERIES = [
    "blocked work and the reasons it was blocked",
    "risks that were escalated or left unresolved",
    "sprint health and delivery trend",
    "items carried over between sprints",
]


def recall_memory(state: PulseState) -> dict:
    """Retrieve what SprintPulse already knows about this project.

    Several targeted queries are used instead of one broad query so the vector
    search surfaces blockers, risks and health history rather than whichever
    topic happens to dominate the store.
    """
    settings = settings_for(state)
    memory = memory_for(state, settings)

    records = []
    seen: set[str] = set()
    for query in RECALL_QUERIES:
        for record in memory.search(query, top_k=6):
            key = record.memory_id or record.text
            if key not in seen:
                seen.add(key)
                records.append(record)

    update: dict = {"memories": records}
    if memory.is_degraded:
        update["errors"] = [
            error("recall_memory", "memory", f"Mem0 unavailable: {memory.degraded_reason}")
        ]
        update["degraded"] = [
            f"Memory served from the local fallback store ({memory.degraded_reason})."
        ]
    return update


def _entries(state: PulseState) -> list[MemoryEntry]:
    """Everything worth remembering from this run, as plain sentences."""
    current = state["current_sprint"]
    metrics = state["metrics"]
    sprint_no = current.meta.number
    project = current.meta.project_name
    entries: list[MemoryEntry] = [
        MemoryEntry(
            kind="health",
            sprint=sprint_no,
            text=(
                f"Sprint {sprint_no} of {project} finished the reporting period with health "
                f"'{metrics.health.value}' (score {metrics.health_score}/100), "
                f"{metrics.done}/{metrics.total_tasks} tasks done and "
                f"{metrics.points_completion_pct}% of story points delivered."
            ),
        )
    ]

    for blocker in state.get("blockers", [])[:6]:
        blocker: Blocker
        age = f" for {blocker.days_blocked} days" if blocker.days_blocked else ""
        entries.append(
            MemoryEntry(
                kind="blocker",
                sprint=sprint_no,
                text=(
                    f"In Sprint {sprint_no}, task {blocker.task_id} ({blocker.title}) owned by "
                    f"{blocker.owner} was a {blocker.kind} blocker{age}: {blocker.reason}"
                ),
            )
        )

    approvals = {a.risk_id: a for a in state.get("approvals", [])}
    for risk in state.get("risks", []):
        risk: Risk
        decision = approvals.get(risk.id)
        suffix = f" A human {decision.decision}d this recommendation." if decision else ""
        entries.append(
            MemoryEntry(
                kind="risk",
                sprint=sprint_no,
                text=(
                    f"Sprint {sprint_no} risk '{risk.title}' was rated {risk.severity.value} "
                    f"({risk.category}). Recommended action: {risk.recommendation}.{suffix}"
                ),
            )
        )

    for issue in state.get("recurring_issues", []):
        entries.append(
            MemoryEntry(
                kind="recurring",
                sprint=sprint_no,
                text=(
                    f"{issue.task_id} ({issue.title}) has now been open across sprints "
                    f"{', '.join(str(s) for s in issue.sprints_seen)} - "
                    f"{issue.days_outstanding} days outstanding. {issue.detail}"
                ),
            )
        )

    for note in state.get("human_context", []):
        entries.append(
            MemoryEntry(kind="human_note", sprint=sprint_no, text=f"Sprint {sprint_no} reviewer note: {note}")
        )

    return entries


def persist_memory(state: PulseState) -> dict:
    """Write this sprint's findings so the next run can compare against them."""
    settings = settings_for(state)
    memory = memory_for(state, settings)
    try:
        written = memory.write(_entries(state))
    except Exception as exc:  # noqa: BLE001 - never fail a finished report on a write
        return {
            "memory_writes": 0,
            "status": "completed",
            "errors": [error("persist_memory", "memory", f"Memory write failed: {exc}")],
            "degraded": ["This sprint was not saved to memory; trend history may have a gap."],
        }

    update: dict = {"memory_writes": written, "status": "completed"}
    if memory.is_degraded:
        update["errors"] = [error("persist_memory", "memory", f"Mem0 unavailable: {memory.degraded_reason}")]
        update["degraded"] = [f"Memory written to the local fallback store ({memory.degraded_reason})."]
    return update
