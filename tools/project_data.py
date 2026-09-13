"""Project-management data tools.

The graph only ever talks to the :class:`ProjectDataSource` interface, so the
JSON files used by this MVP can be swapped for a Jira/Asana/Notion client
without touching any node.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from state import Sprint, SprintMeta, Task


class ProjectDataError(RuntimeError):
    """Base class for every data-layer failure."""


class SprintNotFound(ProjectDataError):
    pass


class MalformedSprintData(ProjectDataError):
    pass


@dataclass(frozen=True)
class SprintSummary:
    """Lightweight catalogue entry used to populate pickers."""

    number: int
    name: str
    ok: bool
    error: str = ""


class ProjectDataSource(ABC):
    """Read-only view of a project tracker.

    A Jira adapter would implement exactly these four methods against the Jira
    REST API and everything downstream would keep working.
    """

    @abstractmethod
    def list_sprints(self, project_id: str) -> list[int]:
        """Sprint numbers available for a project, ascending."""

    @abstractmethod
    def get_sprint(self, project_id: str, sprint_number: int) -> Sprint:
        """One sprint, fully parsed and validated."""

    def get_current_sprint(self, project_id: str, sprint_number: Optional[int] = None) -> Sprint:
        """The requested sprint, or the latest one when no number is given."""
        if sprint_number is None:
            available = self.list_sprints(project_id)
            if not available:
                raise SprintNotFound(f"No sprints found for project '{project_id}'.")
            sprint_number = available[-1]
        return self.get_sprint(project_id, sprint_number)

    def get_previous_sprint(self, project_id: str, sprint_number: int) -> Optional[Sprint]:
        """The sprint immediately before ``sprint_number``, if it exists."""
        earlier = [n for n in self.list_sprints(project_id) if n < sprint_number]
        if not earlier:
            return None
        return self.get_sprint(project_id, max(earlier))

    def describe_sprints(self, project_id: str) -> list[SprintSummary]:
        """Catalogue every sprint, flagging the ones that fail validation."""
        summaries: list[SprintSummary] = []
        for number in self.list_sprints(project_id):
            try:
                sprint = self.get_sprint(project_id, number)
            except ProjectDataError as exc:
                summaries.append(SprintSummary(number, f"Sprint {number}", False, str(exc)))
            else:
                summaries.append(SprintSummary(number, sprint.meta.name, True))
        return summaries

    def get_project_tasks(self, project_id: str, sprint_number: Optional[int] = None) -> list[Task]:
        """Tasks for one sprint, or every task the source knows about."""
        if sprint_number is not None:
            return self.get_sprint(project_id, sprint_number).tasks
        tasks: list[Task] = []
        for number in self.list_sprints(project_id):
            tasks.extend(self.get_sprint(project_id, number).tasks)
        return tasks


class JSONProjectDataSource(ProjectDataSource):
    """Reads ``data/sprint_<n>.json`` files that mimic a tracker export."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    # -- internals ---------------------------------------------------------
    def _load_file(self, path: Path) -> dict:
        try:
            with path.open() as handle:
                return json.load(handle)
        except FileNotFoundError as exc:
            raise SprintNotFound(f"Sprint file '{path.name}' does not exist.") from exc
        except json.JSONDecodeError as exc:
            raise MalformedSprintData(f"'{path.name}' is not valid JSON: {exc}") from exc

    def _index(self) -> tuple[dict[tuple[str, int], Path], set[Path]]:
        """Map (project, sprint) to a file, plus the files too broken to place."""
        if not self.data_dir.exists():
            raise ProjectDataError(f"Data directory '{self.data_dir}' is unavailable.")
        index: dict[tuple[str, int], Path] = {}
        unplaceable: set[Path] = set()
        for path in sorted(self.data_dir.glob("*.json")):
            try:
                raw = self._load_file(path)
                project_id = str(raw["project"]["id"])
                number = int(raw["sprint"]["number"])
            except (ProjectDataError, KeyError, TypeError, ValueError):
                # An unreadable file must not hide the healthy ones; it only
                # fails the run if someone actually asks for that sprint.
                unplaceable.add(path)
                continue
            index[(project_id, number)] = path
        return index, unplaceable

    def _parse(self, raw: dict, source: str) -> Sprint:
        try:
            project = raw["project"]
            sprint = raw["sprint"]
            meta = SprintMeta(
                project_id=str(project["id"]),
                project_name=str(project.get("name", project["id"])),
                number=int(sprint["number"]),
                name=str(sprint.get("name", f"Sprint {sprint['number']}")),
                start_date=str(sprint["start_date"]),
                end_date=str(sprint["end_date"]),
                as_of=str(sprint.get("as_of", sprint["end_date"])),
                goal=str(sprint.get("goal", "")),
            )
            tasks = [Task(**task) for task in raw["tasks"]]
        except (KeyError, TypeError) as exc:
            raise MalformedSprintData(f"'{source}' is missing required fields: {exc}") from exc
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            raise MalformedSprintData(f"'{source}' has an invalid task field '{field}': {first['msg']}") from exc
        if not tasks:
            raise MalformedSprintData(f"'{source}' contains no tasks.")
        return Sprint(meta=meta, team=list(raw.get("team", [])), tasks=tasks)

    # -- interface ---------------------------------------------------------
    def list_sprints(self, project_id: str) -> list[int]:
        index, _ = self._index()
        return sorted(number for (pid, number) in index if pid == project_id)

    def list_projects(self) -> list[str]:
        index, _ = self._index()
        return sorted({pid for (pid, _) in index})

    def get_sprint(self, project_id: str, sprint_number: int) -> Sprint:
        index, unplaceable = self._index()
        path = index.get((project_id, sprint_number))
        if path is None:
            # A file so broken it could not be catalogued should surface its own
            # parse error rather than a misleading "not found".
            candidate = self.data_dir / f"sprint_{sprint_number}.json"
            if candidate not in unplaceable:
                raise SprintNotFound(f"Sprint {sprint_number} not found for project '{project_id}'.")
            path = candidate
        return self._parse(self._load_file(path), path.name)
