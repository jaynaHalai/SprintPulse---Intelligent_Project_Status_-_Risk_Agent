"""Node 6 - risk assessment (LLM judgement with a deterministic safety net)."""

from __future__ import annotations

from analytics import rule_based_risks
from llm import LLMUnavailable, build_model, structured_invoke
from nodes.common import error, settings_for
from state import PulseState, Risk, RiskAssessment, Severity

SYSTEM_PROMPT = """You are a delivery risk analyst for software projects.
You are given verified, pre-computed facts about a sprint: metrics, detected
blockers, week-over-week trends, recurring issues and memories from previous
sprints. Identify the risks that a project manager needs to act on this week.

Rules:
- Only use the facts provided. Never invent task IDs, dates or people.
- Quote concrete evidence (task IDs, day counts, percentages) for every risk.
- Severity reflects impact on the sprint goal and delivery date.
- Set requires_escalation to true only when a human decision or an outside
  party is needed - not for work the team can simply get on with.
- Prefer four to six well-argued risks over an exhaustive list.
- Recommendations must be specific and actionable, naming the task or owner."""


def _facts(state: PulseState) -> str:
    metrics = state["metrics"]
    sprint = state["current_sprint"]
    lines = [
        f"PROJECT: {sprint.meta.project_name}",
        f"SPRINT: {sprint.meta.name} ({sprint.meta.start_date} to {sprint.meta.end_date}), "
        f"reporting as of {sprint.meta.as_of}, {metrics.days_remaining} days remaining.",
        f"GOAL: {sprint.meta.goal}",
        "",
        "METRICS:",
        f"- Health {metrics.health.value} (score {metrics.health_score}/100)",
        f"- {metrics.done}/{metrics.total_tasks} tasks done, {metrics.in_progress} in progress, "
        f"{metrics.todo} not started, {metrics.blocked} blocked",
        f"- {metrics.points_completion_pct}% of {metrics.total_points} story points delivered; "
        f"{metrics.blocked_points} points blocked",
        f"- {metrics.overdue_tasks} overdue task(s), {metrics.unowned_tasks} open task(s) with no owner",
        "- Health drivers: " + "; ".join(metrics.health_reasons or ["none"]),
        "",
        "DETECTED BLOCKERS:",
    ]
    for blocker in state.get("blockers", []):
        lines.append(
            f"- [{blocker.kind}] {blocker.task_id} {blocker.title} (owner {blocker.owner}, "
            f"{blocker.priority.value} priority, {blocker.blocking_points} pts, "
            f"{blocker.days_blocked} days blocked, carried over: {blocker.carried_over}): {blocker.reason}"
        )

    lines += ["", "WEEK-OVER-WEEK TRENDS:"]
    for trend in state.get("trends", []):
        lines.append(f"- {trend.label}: {trend.previous} -> {trend.current} ({trend.direction})")

    lines += ["", "RECURRING ISSUES:"]
    for issue in state.get("recurring_issues", []):
        lines.append(
            f"- {issue.task_id} {issue.title}: seen in sprints "
            f"{', '.join(str(s) for s in issue.sprints_seen)}, {issue.days_outstanding} days outstanding. "
            f"{issue.detail}"
        )

    lines += ["", "MEMORY FROM PREVIOUS SPRINTS:"]
    for record in state.get("memories", [])[:12]:
        lines.append(f"- {record.text}")

    notes = state.get("human_context", [])
    if notes:
        lines += ["", "REVIEWER FEEDBACK ON THE PREVIOUS DRAFT (address this explicitly):"]
        lines += [f"- {note}" for note in notes]

    return "\n".join(lines)


def _guardrail_risks(llm_risks: list[Risk], fallback: list[Risk]) -> list[Risk]:
    """Re-add deterministic critical risks the model failed to mention."""
    mentioned = " ".join(
        [risk.title + " " + " ".join(risk.evidence) + " " + risk.recommendation for risk in llm_risks]
    )
    missing = [
        risk
        for risk in fallback
        if risk.severity == Severity.critical
        and risk.id.replace("RISK-", "").split("-")[-1] not in mentioned
    ]
    return llm_risks + missing


def assess_risk(state: PulseState) -> dict:
    """Rate and explain the risks, falling back to rules if the model fails."""
    settings = settings_for(state)
    sprint = state.get("current_sprint")
    metrics = state.get("metrics")
    if sprint is None or metrics is None:
        return {"risks": []}

    fallback = rule_based_risks(sprint, metrics, state.get("blockers", []), state.get("recurring_issues", []))

    try:
        model = build_model(settings)
        assessment = structured_invoke(
            model,
            RiskAssessment,
            SYSTEM_PROMPT,
            _facts(state),
            label="risk assessment",
        )
        risks = [risk.model_copy(update={"source": "llm"}) for risk in assessment.risks]
        if not risks:
            raise LLMUnavailable("The model returned an empty risk register.")
        return {"risks": _guardrail_risks(risks, fallback)}
    except LLMUnavailable as exc:
        return {
            "risks": fallback,
            "errors": [error("assess_risk", "llm", str(exc), attempts=2)],
            "degraded": ["Risks were derived from deterministic rules because the LLM was unavailable."],
        }
