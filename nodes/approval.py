"""Node 7 - human review of escalating recommendations (LangGraph interrupt)."""

from __future__ import annotations

from langgraph.types import interrupt

from state import ApprovalDecision, Escalation, PulseState, Risk, Severity

MAX_REVISIONS = 1


def needs_human_review(state: PulseState) -> bool:
    """True when at least one risk asks for a human decision."""
    return any(_is_escalating(risk) for risk in state.get("risks", []))


def _is_escalating(risk: Risk) -> bool:
    return risk.requires_escalation or risk.severity in (Severity.high, Severity.critical)


def _escalations(state: PulseState) -> list[Escalation]:
    already_decided = {a.risk_id for a in state.get("approvals", []) if a.decision != "revise"}
    return [
        Escalation(
            risk_id=risk.id,
            title=risk.title,
            severity=risk.severity,
            rationale=" ".join(risk.evidence) or "No supporting evidence recorded.",
            proposed_action=risk.recommendation,
        )
        for risk in state.get("risks", [])
        if _is_escalating(risk) and risk.id not in already_decided
    ]


def _normalise(raw, escalations: list[Escalation]) -> list[ApprovalDecision]:
    """Accept the resume payload in whichever convenient shape it arrives."""
    if isinstance(raw, ApprovalDecision):
        return [raw]
    if isinstance(raw, dict) and "decisions" in raw:
        raw = raw["decisions"]
    if isinstance(raw, str):  # e.g. Command(resume="approve") approves everything
        return [ApprovalDecision(risk_id=e.risk_id, decision=raw) for e in escalations]  # type: ignore[arg-type]
    if isinstance(raw, dict):
        raw = [raw]
    decisions: list[ApprovalDecision] = []
    for item in raw or []:
        decisions.append(item if isinstance(item, ApprovalDecision) else ApprovalDecision(**item))
    return decisions


def request_approval(state: PulseState) -> dict:
    """Pause the graph and hand the escalating risks to a human.

    The run stops here until the caller resumes with decisions. Approved
    recommendations reach the final report as committed actions, rejected ones
    are recorded but not actioned, and 'revise' sends the reviewer's note back
    into the risk assessment for one more pass.
    """
    escalations = _escalations(state)
    if not escalations:
        # Nothing left to review - clear any earlier revision request so the
        # graph cannot loop back into the analysis a second time.
        return {"review_required": False, "revision_requested": False}

    payload = interrupt(
        {
            "type": "risk_review",
            "sprint": state["current_sprint"].meta.name,
            "health": state["metrics"].health.value,
            "instructions": "Reply with one decision per risk: approve, reject or revise (with a note).",
            "escalations": [item.model_dump(mode="json") for item in escalations],
        }
    )

    decisions = _normalise(payload, escalations)
    notes = [d.note.strip() for d in decisions if d.decision == "revise" and d.note.strip()]
    revisions = state.get("revision_count", 0)
    # A revision only re-runs the analysis while there is budget left; after
    # that the note still reaches the report, but the loop cannot spin.
    revise_now = bool(notes) and revisions < MAX_REVISIONS

    update: dict = {
        "escalations": escalations,
        "approvals": (state.get("approvals", []) or []) + decisions,
        "review_required": True,
        "revision_requested": revise_now,
        "revision_count": revisions + 1 if revise_now else revisions,
    }
    if notes:
        update["human_context"] = notes
    return update


def route_after_approval(state: PulseState) -> str:
    """Send a revision request back through risk assessment, at most once."""
    return "assess_risk" if state.get("revision_requested") else "compose_report"
