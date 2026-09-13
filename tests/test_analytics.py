"""Deterministic analysis: metrics, blockers and week-over-week comparison."""

from __future__ import annotations

import pytest

from analytics import (
    compare_sprints,
    compute_metrics,
    detect_blockers,
    find_recurring_issues,
    identify_key_wins,
    rule_based_risks,
)
from state import Health, Severity


# 1. Normal sprint analysis -------------------------------------------------
def test_metrics_match_the_dataset(source):
    sprint = source.get_sprint("atlas", 5)
    metrics = compute_metrics(sprint)

    assert metrics.total_tasks == len(sprint.tasks)
    assert metrics.done == len([t for t in sprint.tasks if t.status == "done"])
    assert metrics.blocked == 1
    assert metrics.total_points == sum(t.story_points for t in sprint.tasks)
    assert 0 <= metrics.health_score <= 100
    assert metrics.health in (Health.at_risk, Health.critical)
    assert metrics.health_reasons, "health score must explain itself"


def test_healthy_sprint_scores_well(source):
    sprint = source.get_sprint("atlas", 4)
    for task in sprint.tasks:
        task.status = "done"
    metrics = compute_metrics(sprint)
    assert metrics.health is Health.healthy
    assert metrics.health_score == 100
    assert metrics.blocked == 0


# 2. Blocked task detection -------------------------------------------------
def test_explicit_blocker_is_detected_with_age(source):
    sprint = source.get_sprint("atlas", 5)
    blockers = {b.task_id: b for b in detect_blockers(sprint, source.get_sprint("atlas", 4))}

    gateway = blockers["ATL-204"]
    assert gateway.kind == "explicit"
    assert gateway.days_blocked == 18  # 2026-08-25 -> 2026-09-12
    assert gateway.carried_over is True
    assert gateway.priority is Severity.critical


def test_implicit_blockers_are_inferred(source):
    sprint = source.get_sprint("atlas", 5)
    blockers = {b.task_id: b.kind for b in detect_blockers(sprint, None)}

    assert blockers["ATL-211"] == "dependency"  # waits on the blocked gateway task
    assert blockers["ATL-205"] == "stale"  # in progress, no update for 10 days
    assert blockers["ATL-212"] == "overdue"
    assert "ATL-213" not in blockers  # done tasks are never blockers


def test_rule_based_risks_cover_critical_blockers(source):
    sprint = source.get_sprint("atlas", 5)
    previous = source.get_sprint("atlas", 4)
    blockers = detect_blockers(sprint, previous)
    recurring = find_recurring_issues(sprint, previous)
    risks = rule_based_risks(sprint, compute_metrics(sprint), blockers, recurring)

    ids = {risk.id for risk in risks}
    assert "RISK-ATL-204" in ids
    assert any(risk.severity is Severity.critical for risk in risks)
    assert all(risk.recommendation for risk in risks)
    assert len(ids) == len(risks), "risk ids must be unique"


# 3. Historical / previous-sprint comparison --------------------------------
def test_week_over_week_comparison(source):
    current = compute_metrics(source.get_sprint("atlas", 5))
    previous = compute_metrics(source.get_sprint("atlas", 4))
    trends = {t.label: t for t in compare_sprints(current, previous)}

    assert trends["Task completion"].direction == "declining"
    assert trends["Blocked story points"].direction == "improving"  # 13 -> 8
    assert "Sprint 4" in trends["Health score"].commentary


def test_first_sprint_has_a_baseline_trend(source):
    trends = compare_sprints(compute_metrics(source.get_sprint("atlas", 4)), None)
    assert len(trends) == 1 and trends[0].direction == "flat"


def test_recurring_issues_span_sprints(source):
    current = source.get_sprint("atlas", 5)
    issues = {i.task_id: i for i in find_recurring_issues(current, source.get_sprint("atlas", 4))}

    assert set(issues) == {"ATL-204", "ATL-205", "ATL-208"}
    assert issues["ATL-204"].sprints_seen == [4, 5]
    assert issues["ATL-204"].days_outstanding == 18
    assert "ATL-207" not in issues, "work finished this sprint is not recurring"


def test_key_wins_highlight_unblocked_work(source):
    wins = identify_key_wins(source.get_sprint("atlas", 5), source.get_sprint("atlas", 4))
    assert any("ATL-209" in win and "unblocked" in win for win in wins)


@pytest.mark.parametrize("sprint_number", [4, 5])
def test_metrics_are_reproducible(source, sprint_number):
    sprint = source.get_sprint("atlas", sprint_number)
    assert compute_metrics(sprint) == compute_metrics(sprint)
