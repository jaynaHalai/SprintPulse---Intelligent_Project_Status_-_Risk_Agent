"""Helpers shared by the graph nodes."""

from __future__ import annotations

from pathlib import Path

from config import Settings, get_settings
from state import NodeError, PulseState
from tools.memory import ProjectMemory
from tools.project_data import JSONProjectDataSource, ProjectDataSource


def settings_for(state: PulseState) -> Settings:
    """Settings for this run, including any injected test faults."""
    return get_settings(tuple(state.get("faults") or []))


def data_source_for(settings: Settings) -> ProjectDataSource:
    """Swap this factory for a Jira/Asana/Notion adapter to go live."""
    return JSONProjectDataSource(Path(settings.data_dir))


def memory_for(state: PulseState, settings: Settings) -> ProjectMemory:
    return ProjectMemory(
        settings.memory,
        project_id=state.get("project_id", "unknown"),
        fault="memory" in settings.faults,
    )


def error(node: str, kind: str, message: str, severity: str = "degraded", attempts: int = 1) -> NodeError:
    return NodeError(node=node, kind=kind, message=message, severity=severity, attempts=attempts)  # type: ignore[arg-type]
