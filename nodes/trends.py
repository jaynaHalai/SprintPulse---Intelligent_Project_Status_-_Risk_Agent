"""Node 5 - week-over-week trend and recurring-issue analysis."""

from __future__ import annotations

from analytics import compare_sprints, find_recurring_issues
from state import PulseState


def trend_analysis(state: PulseState) -> dict:
    """Combine this sprint, the previous sprint and memory into trend signals."""
    metrics = state.get("metrics")
    current = state.get("current_sprint")
    if metrics is None or current is None:
        return {"trends": [], "recurring_issues": []}

    trends = compare_sprints(metrics, state.get("previous_metrics"))
    recurring = find_recurring_issues(
        current, state.get("previous_sprint"), state.get("memories", [])
    )
    return {"trends": trends, "recurring_issues": recurring}
