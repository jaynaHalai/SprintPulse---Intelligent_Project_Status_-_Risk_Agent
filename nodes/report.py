"""Node 8 - compose the weekly status report."""

from __future__ import annotations

from datetime import datetime

from analytics import identify_key_wins
from llm import LLMUnavailable, build_model, structured_invoke
from nodes.common import error, settings_for
from state import ApprovalDecision, PulseState, ReportNarrative, Severity, WeeklyReport

SYSTEM_PROMPT = """You write the weekly status report a project manager sends to
stakeholders. You are given verified facts plus the decisions a human reviewer
made about escalating risks.

Rules:
- Use only the facts provided; never invent progress, names or dates.
- The headline is one sentence a busy executive can act on.
- The executive summary is two or three sentences: where the sprint stands,
  what is holding it up, and what happens next.
- Do not recommend an action a reviewer rejected. Honour reviewer notes.
- Be direct about bad news; no filler, no apologies, no marketing tone."""


def _latest_decisions(state: PulseState) -> dict[str, ApprovalDecision]:
    decisions: dict[str, ApprovalDecision] = {}
    for decision in state.get("approvals", []):
        decisions[decision.risk_id] = decision  # later decisions win
    return decisions


def _facts(state: PulseState, wins: list[str], decisions: dict[str, ApprovalDecision]) -> str:
    metrics = state["metrics"]
    sprint = state["current_sprint"]
    lines = [
        f"PROJECT: {sprint.meta.project_name} / {sprint.meta.name}",
        f"PERIOD: {sprint.meta.start_date} to {sprint.meta.end_date} (as of {sprint.meta.as_of})",
        f"GOAL: {sprint.meta.goal}",
        f"HEALTH: {metrics.health.value} ({metrics.health_score}/100)",
        f"PROGRESS: {metrics.done}/{metrics.total_tasks} tasks done, {metrics.blocked} blocked, "
        f"{metrics.points_completion_pct}% of story points delivered, {metrics.days_remaining} days left.",
        "",
        "KEY WINS:",
        *[f"- {win}" for win in wins],
        "",
        "BLOCKERS:",
        *[
            f"- {b.task_id} {b.title} ({b.kind}, {b.days_blocked} days, owner {b.owner}): {b.reason}"
            for b in state.get("blockers", [])
        ],
        "",
        "RISKS AND REVIEWER DECISIONS:",
    ]
    for risk in state.get("risks", []):
        decision = decisions.get(risk.id)
        verdict = f"reviewer {decision.decision}d" if decision else "no review required"
        note = f" Reviewer note: {decision.note}" if decision and decision.note else ""
        lines.append(
            f"- [{risk.severity.value}] {risk.title} -> proposed: {risk.recommendation} ({verdict}).{note}"
        )

    lines += ["", "TRENDS:"]
    lines += [f"- {t.label}: {t.previous} -> {t.current} ({t.direction})" for t in state.get("trends", [])]
    lines += ["", "RECURRING ISSUES:"]
    lines += [
        f"- {i.task_id} {i.title}: sprints {', '.join(str(s) for s in i.sprints_seen)}, "
        f"{i.days_outstanding} days outstanding"
        for i in state.get("recurring_issues", [])
    ]
    return "\n".join(lines)


def _template_narrative(state: PulseState, wins: list[str]) -> ReportNarrative:
    metrics = state["metrics"]
    sprint = state["current_sprint"]
    blockers = state.get("blockers", [])
    top = blockers[0] if blockers else None
    headline = (
        f"{sprint.meta.name} is {metrics.health.value.replace('_', ' ')} with "
        f"{metrics.done}/{metrics.total_tasks} tasks complete and {metrics.blocked} blocked."
    )
    summary = (
        f"{metrics.points_completion_pct}% of {metrics.total_points} story points are delivered with "
        f"{metrics.days_remaining} day(s) left in the sprint. "
        + (
            f"The most pressing blocker is {top.task_id} ({top.title}): {top.reason} "
            if top
            else "No blockers are currently recorded. "
        )
        + f"Health score is {metrics.health_score}/100."
    )
    return ReportNarrative(
        headline=headline,
        executive_summary=summary,
        key_wins=wins,
        watch_items=[f"{b.task_id} {b.title}" for b in blockers[:3]],
        recommended_actions=[],
    )


def compose_report(state: PulseState) -> dict:
    """Assemble the final report, honouring the reviewer's decisions."""
    settings = settings_for(state)
    sprint = state.get("current_sprint")
    metrics = state.get("metrics")
    if sprint is None or metrics is None:
        return {"status": "failed", "halt_reason": "Report requested without analysis results."}

    wins = identify_key_wins(sprint, state.get("previous_sprint"))
    decisions = _latest_decisions(state)

    narrative_source = "template"
    degraded: list[str] = []
    errors = []
    try:
        model = build_model(settings)
        narrative = structured_invoke(
            model, ReportNarrative, SYSTEM_PROMPT, _facts(state, wins, decisions), label="report narrative"
        )
        narrative_source = "llm"
    except LLMUnavailable as exc:
        narrative = _template_narrative(state, wins)
        errors.append(error("compose_report", "llm", str(exc), attempts=2))
        degraded.append("Report narrative written from the deterministic template (LLM unavailable).")

    # Approved recommendations become committed actions; rejected ones do not.
    actions: list[str] = []
    needs_attention: list[str] = []
    for risk in state.get("risks", []):
        decision = decisions.get(risk.id)
        if decision is None:
            actions.append(risk.recommendation)
        elif decision.decision == "approve":
            actions.append(f"[approved] {risk.recommendation}")
            needs_attention.append(f"{risk.title} - escalation approved by reviewer.")
        elif decision.decision == "reject":
            needs_attention.append(
                f"{risk.title} - escalation rejected by reviewer"
                + (f": {decision.note}" if decision.note else ".")
            )
        else:  # revise
            actions.append(f"[revised] {risk.recommendation}")

    for action in narrative.recommended_actions:
        if action not in actions:
            actions.append(action)

    if metrics.unowned_tasks:
        needs_attention.append(f"{metrics.unowned_tasks} open task(s) still have no named owner.")
    for issue in state.get("recurring_issues", []):
        if issue.days_outstanding >= 14:
            needs_attention.append(
                f"{issue.task_id} has been outstanding {issue.days_outstanding} days across "
                f"{len(issue.sprints_seen)} sprints - decide to escalate, re-scope or drop it."
            )

    report = WeeklyReport(
        project_name=sprint.meta.project_name,
        sprint_name=sprint.meta.name,
        period=f"{sprint.meta.start_date} to {sprint.meta.end_date} (as of {sprint.meta.as_of})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        health=metrics.health,
        headline=narrative.headline,
        executive_summary=narrative.executive_summary,
        progress={
            "Tasks complete": f"{metrics.done}/{metrics.total_tasks} ({metrics.completion_pct}%)",
            "Story points": f"{metrics.done_points}/{metrics.total_points} ({metrics.points_completion_pct}%)",
            "In progress": metrics.in_progress,
            "Not started": metrics.todo,
            "Blocked": f"{metrics.blocked} task(s), {metrics.blocked_points} points",
            "Overdue": metrics.overdue_tasks,
            "Days remaining": metrics.days_remaining,
            "Health score": f"{metrics.health_score}/100",
        },
        key_wins=narrative.key_wins or wins,
        blockers=state.get("blockers", []),
        risks=sorted(state.get("risks", []), key=lambda r: -Severity(r.severity).rank),
        trends=state.get("trends", []),
        recurring_issues=state.get("recurring_issues", []),
        recommended_actions=actions,
        needs_attention=needs_attention + (narrative.watch_items if narrative_source == "llm" else []),
        narrative_source=narrative_source,  # type: ignore[arg-type]
        degraded_notes=(state.get("degraded", []) or []) + degraded,
    )

    update: dict = {"report": report}
    if errors:
        update["errors"] = errors
    if degraded:
        update["degraded"] = degraded
    return update
