"""LangGraph wiring for the SprintPulse agent.

    START
      └─ ingest_project_data ──(critical data failure)──▶ END
             └─ analyze_sprint ─ detect_blockers ─ recall_memory
                    └─ trend_analysis ─ assess_risk
                            ├─(no escalating risk)─────▶ compose_report
                            └─(escalating risk)─ request_approval  ⟲ interrupt
                                    ├─(revise + note)──▶ assess_risk   (max once)
                                    └─(approve/reject)─▶ compose_report
                                                              └─ persist_memory ─ END
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from nodes import (
    analyze_sprint,
    assess_risk,
    compose_report,
    detect_blockers_node,
    ingest_project_data,
    needs_human_review,
    persist_memory,
    recall_memory,
    request_approval,
    route_after_approval,
    trend_analysis,
)
import state as state_models
from state import ApprovalDecision, PulseState

# The checkpoint serialiser is locked to the domain models this graph actually
# stores, instead of trusting whatever a checkpoint happens to contain.
_STATE_TYPES = [
    obj
    for name, obj in vars(state_models).items()
    if isinstance(obj, type) and obj.__module__ == "state" and not name.startswith("_")
]
CHECKPOINT_SERDE = JsonPlusSerializer(allowed_msgpack_modules=None).with_msgpack_allowlist(_STATE_TYPES)


def new_checkpointer() -> InMemorySaver:
    """An in-process checkpointer; swap for SqliteSaver to persist threads."""
    return InMemorySaver(serde=CHECKPOINT_SERDE)


def route_after_ingest(state: PulseState) -> str:
    """A missing or unreadable sprint is fatal; nothing downstream can run."""
    return END if state.get("status") == "failed" else "analyze_sprint"


def route_after_risk(state: PulseState) -> str:
    """Only interrupt for a human when a risk actually needs a decision."""
    return "request_approval" if needs_human_review(state) else "compose_report"


def build_graph(checkpointer: Optional[Any] = None):
    """Compile the workflow. A checkpointer is required for human review."""
    graph = StateGraph(PulseState)

    graph.add_node("ingest_project_data", ingest_project_data)
    graph.add_node("analyze_sprint", analyze_sprint)
    graph.add_node("detect_blockers", detect_blockers_node)
    graph.add_node("recall_memory", recall_memory)
    graph.add_node("trend_analysis", trend_analysis)
    graph.add_node("assess_risk", assess_risk)
    graph.add_node("request_approval", request_approval)
    graph.add_node("compose_report", compose_report)
    graph.add_node("persist_memory", persist_memory)

    graph.add_edge(START, "ingest_project_data")
    graph.add_conditional_edges(
        "ingest_project_data", route_after_ingest, {"analyze_sprint": "analyze_sprint", END: END}
    )
    graph.add_edge("analyze_sprint", "detect_blockers")
    graph.add_edge("detect_blockers", "recall_memory")
    graph.add_edge("recall_memory", "trend_analysis")
    graph.add_edge("trend_analysis", "assess_risk")
    graph.add_conditional_edges(
        "assess_risk",
        route_after_risk,
        {"request_approval": "request_approval", "compose_report": "compose_report"},
    )
    graph.add_conditional_edges(
        "request_approval",
        route_after_approval,
        {"assess_risk": "assess_risk", "compose_report": "compose_report"},
    )
    graph.add_edge("compose_report", "persist_memory")
    graph.add_edge("persist_memory", END)

    return graph.compile(checkpointer=checkpointer or new_checkpointer())


@dataclass
class RunResult:
    """What a single start/resume of the graph produced."""

    state: dict
    trace: list[str] = field(default_factory=list)
    interrupt: Optional[dict] = None

    @property
    def awaiting_review(self) -> bool:
        return self.interrupt is not None

    @property
    def failed(self) -> bool:
        return self.state.get("status") == "failed"


class SprintPulseAgent:
    """Thin façade over the compiled graph: start, then optionally resume."""

    def __init__(self, checkpointer: Optional[Any] = None):
        self.checkpointer = checkpointer or new_checkpointer()
        self.graph = build_graph(self.checkpointer)

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _consume(self, payload: Any, thread_id: str) -> RunResult:
        config = self._config(thread_id)
        trace: list[str] = []
        interrupt_payload: Optional[dict] = None
        for chunk in self.graph.stream(payload, config=config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    interrupt_payload = update[0].value if update else {}
                else:
                    trace.append(node)
        state = dict(self.graph.get_state(config).values)
        if interrupt_payload is not None:
            state["status"] = "awaiting_review"
        return RunResult(state=state, trace=trace, interrupt=interrupt_payload)

    def start(
        self,
        project_id: str,
        sprint_number: Optional[int] = None,
        faults: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
    ) -> tuple[str, RunResult]:
        thread_id = thread_id or f"{project_id}-{uuid4().hex[:8]}"
        payload = {
            "project_id": project_id,
            "sprint_number": sprint_number,
            "faults": faults or [],
            "status": "running",
            "revision_count": 0,
        }
        return thread_id, self._consume(payload, thread_id)

    def resume(self, thread_id: str, decisions: list[ApprovalDecision] | list[dict] | str) -> RunResult:
        """Resume a paused run with the human's decisions."""
        payload = [
            d.model_dump(mode="json") if isinstance(d, ApprovalDecision) else d
            for d in (decisions if isinstance(decisions, list) else [decisions])
        ] if not isinstance(decisions, str) else decisions
        return self._consume(Command(resume=payload), thread_id)

    def snapshot(self, thread_id: str) -> dict:
        return dict(self.graph.get_state(self._config(thread_id)).values)
