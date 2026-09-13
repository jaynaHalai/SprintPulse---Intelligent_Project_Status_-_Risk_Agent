"""Node 1 - pull sprint data from the project-management tool."""

from __future__ import annotations

from nodes.common import data_source_for, error, settings_for
from resilience import RetryExhausted, call_with_retry
from state import PulseState
from tools.project_data import ProjectDataError


def ingest_project_data(state: PulseState) -> dict:
    """Fetch the selected sprint and its predecessor.

    The current sprint is critical: without it there is nothing to report and
    the graph halts. The previous sprint is optional - losing it only costs the
    week-over-week comparison, so the run continues in a degraded mode.
    """
    settings = settings_for(state)
    source = data_source_for(settings)
    project_id = state.get("project_id", "atlas")
    sprint_number = state.get("sprint_number")

    if "data" in settings.faults:
        return {
            "status": "failed",
            "halt_reason": "Project data source unreachable (injected fault).",
            "errors": [
                error(
                    "ingest_project_data",
                    "data",
                    "Injected fault: the project tracker returned a connection error on every attempt.",
                    severity="critical",
                    attempts=2,
                )
            ],
        }

    try:
        # Only transient I/O is worth retrying; malformed data will not fix itself.
        current = call_with_retry(
            lambda: source.get_current_sprint(project_id, sprint_number),
            attempts=2,
            retry_on=(OSError,),
            label="get_current_sprint",
        )
    except (ProjectDataError, RetryExhausted) as exc:
        return {
            "status": "failed",
            "halt_reason": f"Could not load sprint data: {exc}",
            "errors": [error("ingest_project_data", "data", str(exc), severity="critical")],
        }

    update: dict = {
        "status": "running",
        "current_sprint": current,
        "sprint_number": current.meta.number,
        "project_id": current.meta.project_id,
    }

    try:
        update["previous_sprint"] = source.get_previous_sprint(project_id, current.meta.number)
    except Exception as exc:  # noqa: BLE001 - non-critical by design
        update["previous_sprint"] = None
        update["errors"] = [error("ingest_project_data", "data", f"Previous sprint unavailable: {exc}")]
        update["degraded"] = ["Week-over-week comparison limited: previous sprint could not be loaded."]

    return update
