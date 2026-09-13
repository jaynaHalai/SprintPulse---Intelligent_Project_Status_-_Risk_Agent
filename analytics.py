"""Deterministic sprint analytics.

These are the "measurement" half of the agent: everything here is reproducible
and testable, which keeps the LLM responsible for judgement and narrative
rather than for arithmetic.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional

from state import (
    Blocker,
    Health,
    MemoryRecord,
    RecurringIssue,
    Risk,
    Severity,
    Sprint,
    SprintMetrics,
    Task,
    TrendSignal,
)

_SEVERITY_ORDER = {Severity.low: 0, Severity.medium: 1, Severity.high: 2, Severity.critical: 3}


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_between(later: Optional[date], earlier: Optional[date]) -> int:
    if not later or not earlier:
        return 0
    return max((later - earlier).days, 0)


def is_overdue(task: Task, as_of: Optional[date]) -> bool:
    due = parse_date(task.due_date)
    return bool(due and as_of and task.status != "done" and due < as_of)


def is_stale(task: Task, as_of: Optional[date], stale_days: int) -> bool:
    if task.status != "in_progress":
        return False
    return _days_between(as_of, parse_date(task.last_update)) >= stale_days


def compute_metrics(sprint: Sprint, stale_days: int = 5) -> SprintMetrics:
    """Progress, throughput and a transparent health score for one sprint."""
    tasks = sprint.tasks
    as_of = parse_date(sprint.meta.as_of)
    start = parse_date(sprint.meta.start_date)
    end = parse_date(sprint.meta.end_date)

    done = [t for t in tasks if t.status == "done"]
    in_progress = [t for t in tasks if t.status == "in_progress"]
    todo = [t for t in tasks if t.status == "todo"]
    blocked = [t for t in tasks if t.status == "blocked"]

    total_points = sum(t.story_points for t in tasks)
    done_points = sum(t.story_points for t in done)
    blocked_points = sum(t.story_points for t in blocked)
    overdue = [t for t in tasks if is_overdue(t, as_of)]
    stale = [t for t in tasks if is_stale(t, as_of, stale_days)]
    unowned = [t for t in tasks if not t.owner.strip() and t.status != "done"]

    completion_pct = round(100 * len(done) / len(tasks), 1) if tasks else 0.0
    points_pct = round(100 * done_points / total_points, 1) if total_points else 0.0
    days_total = max(_days_between(end, start), 1)
    elapsed = min(_days_between(as_of, start), days_total)
    expected_pct = round(100 * elapsed / days_total, 1)

    score = 100
    reasons: list[str] = []

    shortfall = expected_pct - points_pct
    if shortfall > 0:
        penalty = min(int(shortfall * 0.45), 25)
        score -= penalty
        reasons.append(
            f"{points_pct}% of story points delivered against {expected_pct}% of the sprint elapsed"
        )

    blocked_penalty = 0
    for task in blocked:
        blocked_penalty += {Severity.critical: 15, Severity.high: 8}.get(task.priority, 4)
        reasons.append(f"{task.id} ({task.priority.value}) is blocked: {task.blocker_reason or 'no reason given'}")
    score -= min(blocked_penalty, 30)

    if overdue:
        score -= min(3 * len(overdue), 12)
        reasons.append(f"{len(overdue)} task(s) past their due date")
    if stale:
        score -= min(2 * len(stale), 8)
        reasons.append(f"{len(stale)} in-progress task(s) with no update for {stale_days}+ days")
    if unowned:
        score -= min(2 * len(unowned), 6)
        reasons.append(f"{len(unowned)} open task(s) have no named owner")

    score = max(0, min(100, score))
    if score >= 80:
        health = Health.healthy
    elif score >= 65:
        health = Health.watch
    elif score >= 45:
        health = Health.at_risk
    else:
        health = Health.critical

    return SprintMetrics(
        sprint_number=sprint.meta.number,
        total_tasks=len(tasks),
        done=len(done),
        in_progress=len(in_progress),
        todo=len(todo),
        blocked=len(blocked),
        total_points=total_points,
        done_points=done_points,
        blocked_points=blocked_points,
        completion_pct=completion_pct,
        points_completion_pct=points_pct,
        overdue_tasks=len(overdue),
        unowned_tasks=len(unowned),
        days_remaining=_days_between(end, as_of),
        health=health,
        health_score=score,
        health_reasons=reasons,
    )


def detect_blockers(
    sprint: Sprint,
    previous: Optional[Sprint] = None,
    stale_days: int = 5,
) -> list[Blocker]:
    """Find explicit blockers plus the implicit ones a tracker never labels."""
    as_of = parse_date(sprint.meta.as_of)
    sprint_start = parse_date(sprint.meta.start_date)
    by_id = {task.id: task for task in sprint.tasks}
    previous_ids = {t.id for t in previous.tasks} if previous else set()
    blockers: list[Blocker] = []

    for task in sprint.tasks:
        if task.status == "done":
            continue

        kind: Optional[str] = None
        reasons: list[str] = []
        days_blocked = 0

        if task.status == "blocked":
            kind = "explicit"
            blocked_since = parse_date(task.blocked_since)
            days_blocked = _days_between(as_of, blocked_since)
            reasons.append(task.blocker_reason or "Marked blocked with no reason recorded.")
            if blocked_since and sprint_start and blocked_since < sprint_start:
                reasons.append(f"Blocked since {task.blocked_since}, before this sprint started.")

        unfinished_deps = [
            dep for dep in task.depends_on if dep in by_id and by_id[dep].status != "done"
        ]
        if unfinished_deps:
            kind = kind or "dependency"
            names = ", ".join(f"{dep} ({by_id[dep].status})" for dep in unfinished_deps)
            reasons.append(f"Waiting on unfinished dependency: {names}.")

        if is_stale(task, as_of, stale_days):
            kind = kind or "stale"
            reasons.append(
                f"In progress with no update since {task.last_update} "
                f"({_days_between(as_of, parse_date(task.last_update))} days)."
            )

        if is_overdue(task, as_of):
            kind = kind or "overdue"
            reasons.append(f"Past its due date of {task.due_date}.")

        if kind is None:
            continue

        blockers.append(
            Blocker(
                task_id=task.id,
                title=task.title,
                owner=task.owner or "unassigned",
                priority=task.priority,
                kind=kind,  # type: ignore[arg-type]
                reason=" ".join(reasons),
                days_blocked=days_blocked,
                blocking_points=task.story_points,
                carried_over=task.id in previous_ids,
            )
        )

    blockers.sort(key=lambda b: (-_SEVERITY_ORDER[b.priority], -b.days_blocked))
    return blockers


def compare_sprints(
    current: SprintMetrics,
    previous: Optional[SprintMetrics],
) -> list[TrendSignal]:
    """Week-over-week movement on the numbers that matter."""
    if previous is None:
        return [
            TrendSignal(
                label="Baseline",
                direction="flat",
                current=f"{current.completion_pct}% complete",
                previous="no prior sprint",
                commentary="First tracked sprint; future runs will compare against it.",
            )
        ]

    def signal(label: str, now: float, before: float, higher_is_better: bool, unit: str = "") -> TrendSignal:
        delta = round(now - before, 1)
        if abs(delta) < 0.1:
            direction = "flat"
        elif (delta > 0) == higher_is_better:
            direction = "improving"
        else:
            direction = "declining"
        word = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
        return TrendSignal(
            label=label,
            direction=direction,  # type: ignore[arg-type]
            current=f"{now}{unit}",
            previous=f"{before}{unit}",
            commentary=f"{label} {word} {abs(delta)}{unit} versus Sprint {previous.sprint_number}.",
        )

    return [
        signal("Task completion", current.completion_pct, previous.completion_pct, True, "%"),
        signal("Story point completion", current.points_completion_pct, previous.points_completion_pct, True, "%"),
        signal("Blocked tasks", current.blocked, previous.blocked, False),
        signal("Blocked story points", current.blocked_points, previous.blocked_points, False),
        signal("Overdue tasks", current.overdue_tasks, previous.overdue_tasks, False),
        signal("Health score", current.health_score, previous.health_score, True),
    ]


def find_recurring_issues(
    current: Sprint,
    previous: Optional[Sprint],
    memories: Iterable[MemoryRecord] = (),
) -> list[RecurringIssue]:
    """Work that has survived more than one sprint, from data and from memory."""
    as_of = parse_date(current.meta.as_of)
    issues: dict[str, RecurringIssue] = {}

    previous_by_id = {t.id: t for t in previous.tasks} if previous else {}
    for task in current.tasks:
        if task.status == "done":
            continue
        prior = previous_by_id.get(task.id)
        if prior is None or prior.status == "done":
            continue
        anchor = parse_date(task.blocked_since) or parse_date(previous.meta.start_date if previous else None)
        issues[task.id] = RecurringIssue(
            task_id=task.id,
            title=task.title,
            sprints_seen=[previous.meta.number, current.meta.number] if previous else [current.meta.number],
            days_outstanding=_days_between(as_of, anchor),
            detail=(
                f"Still '{task.status}' after being '{prior.status}' in Sprint "
                f"{previous.meta.number if previous else '-'}."
            ),
            source="data",
        )

    # Memory can surface repeats the current two sprints alone cannot prove.
    for record in memories:
        sprint_no = record.metadata.get("sprint")
        for task_id in issues:
            if task_id in record.text and sprint_no not in issues[task_id].sprints_seen:
                if isinstance(sprint_no, int) and sprint_no not in issues[task_id].sprints_seen:
                    issues[task_id].sprints_seen.append(sprint_no)
                    issues[task_id].source = "memory"

    for issue in issues.values():
        issue.sprints_seen = sorted(set(issue.sprints_seen))

    return sorted(issues.values(), key=lambda i: -i.days_outstanding)


def identify_key_wins(current: Sprint, previous: Optional[Sprint]) -> list[str]:
    """Completed work, highlighting anything that escaped a previous blocker."""
    previous_by_id = {t.id: t for t in previous.tasks} if previous else {}
    wins: list[str] = []
    for task in current.tasks:
        if task.status != "done":
            continue
        prior = previous_by_id.get(task.id)
        if prior and prior.status == "blocked":
            wins.append(f"{task.id} {task.title} - unblocked and delivered ({task.story_points} pts).")
        elif prior and prior.status != "done":
            wins.append(f"{task.id} {task.title} - carried over from Sprint {previous.meta.number} and closed.")
        elif task.priority in (Severity.high, Severity.critical):
            wins.append(f"{task.id} {task.title} - {task.priority.value} priority item delivered.")
    return wins or ["No completed work recorded in this period."]


def rule_based_risks(
    sprint: Sprint,
    metrics: SprintMetrics,
    blockers: list[Blocker],
    recurring: list[RecurringIssue],
) -> list[Risk]:
    """Fallback risk register used when the LLM is unavailable.

    It is also the safety net that guarantees the agent never reports "no
    risks" simply because a model call failed.
    """
    risks: list[Risk] = []
    goal_date = sprint.meta.end_date

    for blocker in blockers:
        if blocker.kind == "explicit" and blocker.priority in (Severity.high, Severity.critical):
            severity = Severity.critical if blocker.priority == Severity.critical else Severity.high
            risks.append(
                Risk(
                    id=f"RISK-{blocker.task_id}",
                    title=f"{blocker.title} blocked for {blocker.days_blocked} days",
                    severity=severity,
                    category="dependency",
                    evidence=[blocker.reason, f"{blocker.blocking_points} story points held up."],
                    recommendation=(
                        f"Escalate {blocker.task_id} to the accountable owner and agree a resolution date "
                        f"before {goal_date}."
                    ),
                    requires_escalation=True,
                )
            )

    covered = {risk.id.replace("RISK-", "") for risk in risks}
    for issue in recurring:
        if issue.task_id in covered:
            continue
        if issue.days_outstanding >= 10:
            risks.append(
                Risk(
                    id=f"RISK-STALE-{issue.task_id}",
                    title=f"{issue.title} has been open across sprints {', '.join(map(str, issue.sprints_seen))}",
                    severity=Severity.high,
                    category="delivery",
                    evidence=[issue.detail, f"Outstanding for {issue.days_outstanding} days."],
                    recommendation=(
                        f"Re-scope, re-assign or explicitly defer {issue.task_id} ({issue.title}) "
                        "at the next planning session."
                    ),
                    requires_escalation=True,
                )
            )

    if metrics.health in (Health.at_risk, Health.critical):
        risks.append(
            Risk(
                id="RISK-DELIVERY",
                title=f"Sprint {metrics.sprint_number} is tracking behind plan",
                severity=Severity.high if metrics.health == Health.at_risk else Severity.critical,
                category="delivery",
                evidence=metrics.health_reasons[:4],
                recommendation=(
                    f"Cut scope to the {metrics.blocked + metrics.in_progress} open items most tied to the "
                    "sprint goal and confirm the end date with stakeholders."
                ),
                requires_escalation=metrics.health == Health.critical,
            )
        )

    if metrics.unowned_tasks:
        risks.append(
            Risk(
                id="RISK-OWNERSHIP",
                title=f"{metrics.unowned_tasks} open task(s) have no owner",
                severity=Severity.medium,
                category="resourcing",
                evidence=[t.id + " " + t.title for t in sprint.tasks if not t.owner.strip() and t.status != "done"],
                recommendation="Assign an owner for every open task before the next standup.",
            )
        )

    return risks
