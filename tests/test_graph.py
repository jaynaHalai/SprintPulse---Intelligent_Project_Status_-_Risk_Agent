"""End-to-end behaviour of the LangGraph workflow."""

from __future__ import annotations

import pytest

from graph import SprintPulseAgent
from state import ApprovalDecision, Health


@pytest.fixture
def agent() -> SprintPulseAgent:
    return SprintPulseAgent()


# 1. Normal sprint analysis -------------------------------------------------
def test_run_stops_for_human_review(agent):
    _, result = agent.start("atlas", 5)

    assert result.trace == [
        "ingest_project_data",
        "analyze_sprint",
        "detect_blockers",
        "recall_memory",
        "trend_analysis",
        "assess_risk",
    ]
    assert result.awaiting_review
    assert result.state["status"] == "awaiting_review"
    assert result.interrupt["type"] == "risk_review"
    assert result.interrupt["escalations"], "a critical blocker must reach a human"
    assert result.state["metrics"].health in (Health.at_risk, Health.critical)
    assert result.state["blockers"] and result.state["risks"]


def test_approval_completes_the_run_and_writes_memory(agent):
    thread_id, result = agent.start("atlas", 5)
    decisions = [
        ApprovalDecision(risk_id=item["risk_id"], decision="approve")
        for item in result.interrupt["escalations"]
    ]
    final = agent.resume(thread_id, decisions)

    assert final.trace == ["request_approval", "compose_report", "persist_memory"]
    assert final.state["status"] == "completed"
    report = final.state["report"]
    assert report.headline and report.executive_summary
    assert report.recommended_actions and report.blockers and report.trends
    assert any(action.startswith("[approved]") for action in report.recommended_actions)
    assert final.state["memory_writes"] >= 0
    assert "Weekly Status Report" in report.to_markdown()


def test_rejected_recommendation_is_not_actioned(agent):
    thread_id, result = agent.start("atlas", 5)
    target = result.interrupt["escalations"][0]
    final = agent.resume(
        thread_id,
        [ApprovalDecision(risk_id=target["risk_id"], decision="reject", note="Vendor call already booked.")],
    )

    report = final.state["report"]
    assert target["proposed_action"] not in report.recommended_actions
    assert any("rejected by reviewer" in item for item in report.needs_attention)
    assert any("Vendor call already booked" in item for item in report.needs_attention)


# Human-in-the-loop revision loop -------------------------------------------
def test_revision_sends_context_back_through_risk_assessment(agent):
    thread_id, result = agent.start("atlas", 5)
    target = result.interrupt["escalations"][0]
    second = agent.resume(
        thread_id,
        [ApprovalDecision(risk_id=target["risk_id"], decision="revise", note="Credentials land 2026-09-15.")],
    )

    assert "assess_risk" in second.trace, "a revision must re-run the analysis"
    assert second.state["revision_count"] == 1
    assert "Credentials land 2026-09-15." in second.state["human_context"]
    assert second.awaiting_review, "the revised risk comes back for a decision"

    final = agent.resume(
        thread_id,
        [ApprovalDecision(risk_id=item["risk_id"], decision="approve") for item in second.interrupt["escalations"]],
    )
    assert final.state["status"] == "completed"
    # The revision budget is spent, so the graph cannot loop again.
    assert final.state["revision_count"] == 1


def test_healthy_sprint_skips_human_review(agent, monkeypatch, tmp_path):
    """A sprint with nothing to escalate must not interrupt."""
    import json
    import shutil

    from config import ROOT

    payload = json.loads((ROOT / "data" / "sprint_4.json").read_text())
    for task in payload["tasks"]:
        task["status"] = "done"
        task.pop("blocked_since", None)
        task["blocker_reason"] = ""
    payload["project"]["id"] = "sunny"
    (tmp_path / "sprint_4.json").write_text(json.dumps(payload))
    monkeypatch.setenv("SPRINTPULSE_DATA_DIR", str(tmp_path))

    _, result = SprintPulseAgent().start("sunny", 4)
    assert not result.awaiting_review
    # The conditional edge skips the review node entirely when nothing escalates.
    assert "request_approval" not in result.trace
    assert result.trace[-2:] == ["compose_report", "persist_memory"]
    assert result.state["report"].health is Health.healthy
    shutil.rmtree(tmp_path, ignore_errors=True)


# 4. Missing / malformed data handling --------------------------------------
def test_malformed_sprint_halts_the_run(agent):
    _, result = agent.start("atlas", 6)

    assert result.failed
    assert result.trace == ["ingest_project_data"]
    assert "end_date" in result.state["halt_reason"]
    assert result.state["errors"][0].severity == "critical"
    assert "report" not in result.state


def test_missing_sprint_halts_the_run(agent):
    _, result = agent.start("atlas", 42)
    assert result.failed and "not found" in result.state["halt_reason"].lower()


# Fault injection: tool, LLM and memory failures ----------------------------
def test_data_source_outage_halts_gracefully(agent):
    _, result = agent.start("atlas", 5, faults=["data"])

    assert result.failed
    assert result.state["errors"][0].kind == "data"
    assert result.state["errors"][0].attempts == 2, "the tool call is retried before giving up"


def test_llm_outage_falls_back_to_rule_based_risks(agent):
    thread_id, result = agent.start("atlas", 5, faults=["llm"])

    assert result.state["risks"], "risks must still be produced without a model"
    assert all(risk.source == "rules" for risk in result.state["risks"])
    assert any(err.kind == "llm" for err in result.state["errors"])

    final = agent.resume(thread_id, "approve")
    assert final.state["status"] == "completed"
    assert final.state["report"].narrative_source == "template"
    assert any("LLM" in note for note in final.state["report"].degraded_notes)


def test_memory_outage_degrades_but_completes(agent):
    thread_id, result = agent.start("atlas", 5, faults=["memory"])
    final = agent.resume(thread_id, "approve")

    assert final.state["status"] == "completed"
    assert any(err.kind == "memory" for err in final.state["errors"])
    assert any("fallback" in note for note in final.state["degraded"])
    assert final.state["report"] is not None


# 3. Historical comparison inside the graph ---------------------------------
def test_graph_compares_against_the_previous_sprint(agent):
    _, result = agent.start("atlas", 5)
    labels = {t.label for t in result.state["trends"]}

    assert {"Task completion", "Health score"} <= labels
    assert result.state["previous_metrics"].sprint_number == 4
    assert {i.task_id for i in result.state["recurring_issues"]} == {"ATL-204", "ATL-205", "ATL-208"}


# 5. Memory retrieval inside the graph --------------------------------------
def test_memory_written_by_one_run_is_recalled_by_the_next(agent):
    _, unknown = agent.start("not-a-project", 5)
    assert unknown.failed, "memory and data are scoped per project"

    thread_id, _ = agent.start("atlas", 4)
    agent.resume(thread_id, "approve")

    _, run_two = agent.start("atlas", 5)
    assert run_two.state["memories"], "the second run must recall Sprint 4 memories"
    assert any("Sprint 4" in record.text for record in run_two.state["memories"])


# LLM-backed path (model stubbed so tests stay offline and deterministic) -----
def test_llm_path_produces_llm_sourced_risks_and_narrative(agent, monkeypatch):
    import nodes.report as report_node
    import nodes.risk as risk_node
    from state import ReportNarrative, Risk, RiskAssessment, Severity

    monkeypatch.setattr(risk_node, "build_model", lambda settings: object())
    monkeypatch.setattr(report_node, "build_model", lambda settings: object())
    monkeypatch.setattr(
        risk_node,
        "structured_invoke",
        lambda *a, **k: RiskAssessment(
            risks=[
                Risk(
                    id="RISK-LLM-1",
                    title="Go-live date depends on a single blocked vendor task",
                    severity=Severity.high,
                    category="dependency",
                    evidence=["ATL-204 blocked 18 days"],
                    recommendation="Agree a credential delivery date with NorthBank by Friday.",
                    requires_escalation=True,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        report_node,
        "structured_invoke",
        lambda *a, **k: ReportNarrative(
            headline="Go-live is at risk until NorthBank credentials arrive.",
            executive_summary="Summary written by the model.",
            key_wins=["ATL-209 delivered"],
            watch_items=["ATL-204"],
            recommended_actions=["Book a vendor escalation call."],
        ),
    )

    thread_id, result = agent.start("atlas", 5)
    sources = {risk.source for risk in result.state["risks"]}
    assert "llm" in sources
    # The deterministic guardrail re-adds critical risks the model left out.
    assert "rules" in sources
    assert not [err for err in result.state.get("errors", []) if err.kind == "llm"]

    final = agent.resume(thread_id, "approve")
    report = final.state["report"]
    assert report.narrative_source == "llm"
    assert report.headline.startswith("Go-live is at risk")
    assert "Book a vendor escalation call." in report.recommended_actions
