"""Data-layer contract: valid sprints load, broken ones fail loudly."""

from __future__ import annotations

import json

import pytest

from tools.project_data import (
    JSONProjectDataSource,
    MalformedSprintData,
    ProjectDataError,
    SprintNotFound,
)


def test_tool_interface(source):
    assert source.list_sprints("atlas") == [4, 5, 6]
    assert source.get_current_sprint("atlas", 5).meta.number == 5
    assert source.get_previous_sprint("atlas", 5).meta.number == 4
    assert source.get_previous_sprint("atlas", 4) is None
    assert len(source.get_project_tasks("atlas", 5)) == 13


# 4. Missing / malformed data handling --------------------------------------
def test_missing_sprint_raises_not_found(source):
    with pytest.raises(SprintNotFound):
        source.get_sprint("atlas", 99)


def test_unknown_project_raises(source):
    with pytest.raises(SprintNotFound):
        source.get_current_sprint("does-not-exist")


def test_malformed_sprint_names_the_problem(source):
    with pytest.raises(MalformedSprintData) as excinfo:
        source.get_sprint("atlas", 6)
    assert "end_date" in str(excinfo.value)


def test_invalid_task_field_is_rejected(tmp_path):
    payload = {
        "project": {"id": "atlas", "name": "Atlas"},
        "sprint": {"number": 9, "name": "S9", "start_date": "2026-01-01", "end_date": "2026-01-14"},
        "tasks": [{"id": "X-1", "title": "Bad status", "status": "in_flight"}],
    }
    (tmp_path / "sprint_9.json").write_text(json.dumps(payload))
    with pytest.raises(MalformedSprintData) as excinfo:
        JSONProjectDataSource(tmp_path).get_sprint("atlas", 9)
    assert "status" in str(excinfo.value)


def test_invalid_json_is_reported(tmp_path):
    (tmp_path / "sprint_1.json").write_text("{not json")
    with pytest.raises(MalformedSprintData):
        JSONProjectDataSource(tmp_path).get_sprint("atlas", 1)


def test_missing_directory_is_reported(tmp_path):
    with pytest.raises(ProjectDataError):
        JSONProjectDataSource(tmp_path / "nope").list_sprints("atlas")


def test_catalogue_flags_broken_sprints(source):
    summaries = {s.number: s for s in source.describe_sprints("atlas")}
    assert summaries[5].ok is True
    assert summaries[6].ok is False and summaries[6].error
