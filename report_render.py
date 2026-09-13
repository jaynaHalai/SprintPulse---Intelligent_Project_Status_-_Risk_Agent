"""Markdown rendering for the weekly report."""

from __future__ import annotations

from state import WeeklyReport

_HEALTH_BADGE = {
    "healthy": "🟢 Healthy",
    "watch": "🟡 Watch",
    "at_risk": "🟠 At risk",
    "critical": "🔴 Critical",
}


def health_badge(health) -> str:
    value = getattr(health, "value", str(health))
    return _HEALTH_BADGE.get(value, value)


def render_markdown(report: WeeklyReport) -> str:
    lines: list[str] = [
        f"# Weekly Status Report - {report.project_name}",
        f"**{report.sprint_name}** · {report.period} · generated {report.generated_at}",
        "",
        f"## Overall health: {health_badge(report.health)}",
        f"**{report.headline}**",
        "",
        report.executive_summary,
        "",
        "## Sprint progress",
    ]
    for key, value in report.progress.items():
        lines.append(f"- **{key}:** {value}")

    lines += ["", "## Key wins"]
    lines += [f"- {win}" for win in report.key_wins]

    lines += ["", "## Blockers"]
    if report.blockers:
        for blocker in report.blockers:
            age = f", {blocker.days_blocked} days" if blocker.days_blocked else ""
            lines.append(
                f"- **{blocker.task_id} {blocker.title}** ({blocker.kind}{age}, owner {blocker.owner}) - {blocker.reason}"
            )
    else:
        lines.append("- None detected.")

    lines += ["", "## Risks"]
    if report.risks:
        for risk in report.risks:
            lines.append(f"- **[{risk.severity.value.upper()}] {risk.title}** ({risk.category})")
            for item in risk.evidence:
                lines.append(f"  - evidence: {item}")
            lines.append(f"  - recommended: {risk.recommendation}")
    else:
        lines.append("- None identified.")

    lines += ["", "## Week-over-week trends"]
    arrow = {"improving": "▲", "declining": "▼", "flat": "▬"}
    for trend in report.trends:
        lines.append(
            f"- {arrow.get(trend.direction, '')} **{trend.label}**: {trend.previous} → {trend.current} - {trend.commentary}"
        )

    lines += ["", "## Recurring and stale issues"]
    if report.recurring_issues:
        for issue in report.recurring_issues:
            sprints = ", ".join(str(s) for s in issue.sprints_seen)
            lines.append(
                f"- **{issue.task_id} {issue.title}** - open across sprints {sprints}, "
                f"{issue.days_outstanding} days outstanding. {issue.detail}"
            )
    else:
        lines.append("- None; nothing has carried over.")

    lines += ["", "## Recommended actions"]
    lines += [f"{i}. {action}" for i, action in enumerate(report.recommended_actions, 1)] or ["- None."]

    lines += ["", "## Needs human attention"]
    lines += [f"- {item}" for item in report.needs_attention] or ["- Nothing outstanding."]

    if report.degraded_notes:
        lines += ["", "## Run notes"]
        lines += [f"- {note}" for note in report.degraded_notes]

    lines += ["", f"_Narrative source: {report.narrative_source}._"]
    return "\n".join(lines)
