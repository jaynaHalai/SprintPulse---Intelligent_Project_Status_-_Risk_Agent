"""Nodes 2 and 3 - sprint metrics and blocker detection."""

from __future__ import annotations

from analytics import compute_metrics, detect_blockers
from nodes.common import error, settings_for
from state import PulseState


def analyze_sprint(state: PulseState) -> dict:
    """Turn raw tasks into progress metrics and a transparent health score."""
    settings = settings_for(state)
    current = state.get("current_sprint")
    if current is None:
        return {
            "status": "failed",
            "halt_reason": "Sprint analysis ran without sprint data.",
            "errors": [error("analyze_sprint", "internal", "current_sprint missing", severity="critical")],
        }

    update: dict = {"metrics": compute_metrics(current, settings.stale_update_days)}
    previous = state.get("previous_sprint")
    update["previous_metrics"] = (
        compute_metrics(previous, settings.stale_update_days) if previous else None
    )
    return update


def detect_blockers_node(state: PulseState) -> dict:
    """Explicit blockers plus dependency, staleness and overdue blockers."""
    settings = settings_for(state)
    current = state.get("current_sprint")
    if current is None:
        return {"blockers": []}
    return {
        "blockers": detect_blockers(current, state.get("previous_sprint"), settings.stale_update_days)
    }
