"""Historical question answering over persistent project memory.

This is the second, smaller agentic path: retrieve from Mem0, call the project
data tool for exact figures, then let the model answer using only those facts.
Without an LLM it still answers from memory plus deterministic analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from analytics import find_recurring_issues
from config import get_settings
from llm import LLMUnavailable, build_model, text_invoke
from state import MemoryRecord, RecurringIssue
from tools.memory import ProjectMemory
from tools.project_data import JSONProjectDataSource, ProjectDataError

SYSTEM_PROMPT = """You answer questions about a software project's history.
You are given memories recorded by the status agent in previous runs, plus
exact figures computed from the tracker.

Rules:
- Answer only from the supplied memories and figures; if they do not cover the
  question, say so plainly and state what is missing.
- Cite task IDs, sprint numbers and day counts wherever they appear.
- Two to five sentences. No preamble, no bullet padding."""


@dataclass
class QAResult:
    answer: str
    sources: list[MemoryRecord] = field(default_factory=list)
    facts: list[RecurringIssue] = field(default_factory=list)
    mode: str = "deterministic"
    notes: list[str] = field(default_factory=list)


def _carryover_facts(project_id: str, sprint_number: Optional[int], settings) -> tuple[list[RecurringIssue], list[str]]:
    """Exact 'stuck for more than one sprint' figures straight from the tool."""
    notes: list[str] = []
    try:
        source = JSONProjectDataSource(settings.data_dir)
        current = source.get_current_sprint(project_id, sprint_number)
        previous = source.get_previous_sprint(project_id, current.meta.number)
        return find_recurring_issues(current, previous), notes
    except ProjectDataError as exc:
        notes.append(f"Tracker figures unavailable: {exc}")
        return [], notes


def _deterministic_answer(question: str, memories: Sequence[MemoryRecord], facts: Sequence[RecurringIssue]) -> str:
    lowered = question.lower()
    wants_stuck = any(
        word in lowered
        for word in ("stuck", "stale", "carry", "carried", "more than one sprint", "recurring")
    )
    wants_health = any(word in lowered for word in ("health", "trend", "changed", "improving", "worse"))
    sections: list[str] = []

    if wants_stuck and facts:
        lines = ["Items open across more than one sprint:"]
        for issue in facts:
            sprints = ", ".join(str(s) for s in issue.sprints_seen)
            lines.append(
                f"- {issue.task_id} {issue.title}: sprints {sprints}, {issue.days_outstanding} days outstanding. {issue.detail}"
            )
        sections.append("\n".join(lines))

    if wants_health:
        health = [r for r in memories if r.metadata.get("kind") == "health"]
        if health:
            sections.append("\n".join(["Sprint health recorded in memory:"] + [f"- {r.text}" for r in health]))

    if not sections and memories:
        sections.append("\n".join(["From project memory:"] + [f"- {record.text}" for record in memories[:6]]))
    elif sections and memories and not wants_stuck:
        pass

    if sections:
        return "\n\n".join(sections)

    return (
        "Nothing in project memory covers that yet. Run the sprint analysis at least once so "
        "SprintPulse has history to answer from."
    )


def answer_question(
    question: str,
    project_id: str = "atlas",
    sprint_number: Optional[int] = None,
    faults: Sequence[str] = (),
    top_k: int = 8,
) -> QAResult:
    """Answer a historical question about the project."""
    settings = get_settings(tuple(faults))
    memory = ProjectMemory(settings.memory, project_id, fault="memory" in settings.faults)

    memories = memory.search(question, top_k=top_k)
    notes: list[str] = []
    if memory.is_degraded:
        notes.append(f"Memory served from the local fallback store ({memory.degraded_reason}).")

    facts, fact_notes = _carryover_facts(project_id, sprint_number, settings)
    notes += fact_notes

    context = ["MEMORIES:"] + [f"- {record.text}" for record in memories]
    context += ["", "CARRY-OVER FIGURES FROM THE TRACKER:"]
    context += [
        f"- {issue.task_id} {issue.title}: sprints {', '.join(str(s) for s in issue.sprints_seen)}, "
        f"{issue.days_outstanding} days outstanding, {issue.detail}"
        for issue in facts
    ] or ["- none"]
    context += ["", f"QUESTION: {question}"]

    try:
        model = build_model(settings)
        answer = text_invoke(model, SYSTEM_PROMPT, "\n".join(context), label="memory question")
        return QAResult(answer=answer.strip(), sources=memories, facts=facts, mode="llm", notes=notes)
    except LLMUnavailable as exc:
        notes.append(f"Answered without the LLM: {exc}")
        return QAResult(
            answer=_deterministic_answer(question, memories, facts),
            sources=memories,
            facts=facts,
            mode="deterministic",
            notes=notes,
        )
