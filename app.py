"""SprintPulse - Streamlit front end for the status and risk agent."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from config import get_settings
from graph import SprintPulseAgent
from qa import answer_question
from report_render import health_badge, render_markdown
from state import ApprovalDecision
from tools.project_data import JSONProjectDataSource, ProjectDataError

st.set_page_config(page_title="SprintPulse", page_icon="📡", layout="wide")

HEALTH_COLOR = {"healthy": "#1a7f37", "watch": "#9a6700", "at_risk": "#bc4c00", "critical": "#b62324"}
EXAMPLE_QUESTIONS = [
    "What has been stuck for more than one sprint?",
    "How has sprint health changed?",
    "Which risks keep recurring?",
]


# --------------------------------------------------------------------------
# session helpers
# --------------------------------------------------------------------------
def agent() -> SprintPulseAgent:
    if "agent" not in st.session_state:
        st.session_state.agent = SprintPulseAgent()
    return st.session_state.agent


def run_analysis(project_id: str, sprint_number: Optional[int], faults: list[str]) -> None:
    thread_id, result = agent().start(project_id, sprint_number, faults)
    st.session_state.thread_id = thread_id
    st.session_state.result = result


def submit_decisions(decisions: list[ApprovalDecision]) -> None:
    st.session_state.result = agent().resume(st.session_state.thread_id, decisions)


def seed_history(project_id: str, sprint_number: int, faults: list[str]) -> int:
    """Run every earlier sprint end-to-end so memory has real history."""
    source = JSONProjectDataSource(get_settings().data_dir)
    seeded = 0
    for number in source.list_sprints(project_id):
        if number >= sprint_number:
            continue
        thread_id, result = agent().start(project_id, number, faults)
        if result.awaiting_review:
            result = agent().resume(thread_id, "approve")
        if not result.failed:
            seeded += 1
    return seeded


# --------------------------------------------------------------------------
# sidebar
# --------------------------------------------------------------------------
settings = get_settings()
source = JSONProjectDataSource(settings.data_dir)

with st.sidebar:
    st.title("📡 SprintPulse")
    st.caption("Intelligent project status & risk agent")

    try:
        projects = source.list_projects()
    except ProjectDataError as exc:
        st.error(f"Data source unavailable: {exc}")
        st.stop()

    project_id = st.selectbox("Project", projects, index=0, key="project")
    summaries = source.describe_sprints(project_id)
    labels = {
        s.number: f"Sprint {s.number} - {s.name}" + ("" if s.ok else "  ⚠ malformed")
        for s in summaries
    }
    healthy = [s.number for s in summaries if s.ok]
    default_index = list(labels).index(healthy[-1]) if healthy else 0
    sprint_number = st.selectbox(
        "Sprint", list(labels), index=default_index, format_func=lambda n: labels[n], key="sprint"
    )

    st.divider()
    st.subheader("Fault injection")
    st.caption("Demonstrate the error paths on demand.")
    faults = []
    if st.checkbox("Project data source offline", key="fault_data"):
        faults.append("data")
    if st.checkbox("LLM provider offline", key="fault_llm"):
        faults.append("llm")
    if st.checkbox("Mem0 offline", key="fault_memory"):
        faults.append("memory")

    st.divider()
    if st.button("▶ Run status analysis", type="primary", use_container_width=True, key="run"):
        with st.spinner("Running the SprintPulse graph…"):
            run_analysis(project_id, sprint_number, faults)
    if st.button("⏪ Seed memory from earlier sprints", use_container_width=True, key="seed"):
        with st.spinner("Replaying earlier sprints into memory…"):
            count = seed_history(project_id, sprint_number, faults)
        st.success(f"Seeded {count} earlier sprint(s) into memory.")

    st.divider()
    st.caption(f"**LLM:** {settings.llm.label}")
    st.caption(f"**Memory:** mem0 ({settings.memory.mode}), infer={settings.memory.infer}")

result = st.session_state.get("result")
if result is None:
    st.title("SprintPulse")
    st.markdown(
        "Select a sprint and press **Run status analysis**. The agent pulls sprint data, "
        "scores sprint health, detects blockers, recalls what it learned in previous runs, "
        "compares week over week, assesses risk, asks you to review anything that needs a "
        "human decision, and then writes a weekly status report."
    )
    st.info("Tip for a first demo: seed memory from earlier sprints, then run Sprint 5.")
    st.stop()

state = result.state

# --------------------------------------------------------------------------
# critical failure
# --------------------------------------------------------------------------
if result.failed:
    st.error(f"Run halted: {state.get('halt_reason', 'unknown error')}")
    st.caption("Nodes executed: " + " → ".join(result.trace))
    for err in state.get("errors", []):
        st.write(f"- **{err.node}** ({err.kind}, {err.severity}, {err.attempts} attempt(s)): {err.message}")
    st.stop()

metrics = state["metrics"]
sprint = state["current_sprint"]

# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
left, right = st.columns([3, 1])
with left:
    st.title(sprint.meta.project_name)
    st.caption(f"{sprint.meta.name} · {sprint.meta.start_date} → {sprint.meta.end_date} · as of {sprint.meta.as_of}")
with right:
    st.markdown(
        f"<div style='text-align:right;font-size:1.6rem;font-weight:700;"
        f"color:{HEALTH_COLOR.get(metrics.health.value, '#333')}'>{health_badge(metrics.health)}</div>"
        f"<div style='text-align:right;color:#666'>health score {metrics.health_score}/100</div>",
        unsafe_allow_html=True,
    )

st.caption("Nodes executed: " + " → ".join(result.trace))

for note in dict.fromkeys(state.get("degraded", [])):
    st.warning(note, icon="⚠️")

# --------------------------------------------------------------------------
# human in the loop
# --------------------------------------------------------------------------
if result.awaiting_review:
    payload = result.interrupt or {}
    st.subheader("🧑‍⚖️ Human review required")
    st.caption(payload.get("instructions", ""))
    with st.form("review"):
        decisions: list[ApprovalDecision] = []
        for item in payload.get("escalations", []):
            st.markdown(f"**[{item['severity'].upper()}] {item['title']}**")
            st.caption(item["rationale"])
            st.markdown(f"*Proposed action:* {item['proposed_action']}")
            choice = st.radio(
                "Decision",
                ["approve", "reject", "revise"],
                horizontal=True,
                key=f"decision_{item['risk_id']}",
            )
            note = st.text_input("Context for the agent (required for 'revise')", key=f"note_{item['risk_id']}")
            decisions.append(ApprovalDecision(risk_id=item["risk_id"], decision=choice, note=note))
            st.divider()
        if st.form_submit_button("Submit decisions", type="primary"):
            with st.spinner("Resuming the graph with your decisions…"):
                submit_decisions(decisions)
            st.rerun()
    st.stop()

# --------------------------------------------------------------------------
# tabs
# --------------------------------------------------------------------------
overview, blockers_tab, risks_tab, trends_tab, report_tab, memory_tab = st.tabs(
    ["Overview", "Blockers", "Risks", "Trends & memory", "Weekly report", "Ask memory"]
)

with overview:
    cols = st.columns(5)
    cols[0].metric("Tasks complete", f"{metrics.done}/{metrics.total_tasks}", f"{metrics.completion_pct}%")
    cols[1].metric("Story points", f"{metrics.done_points}/{metrics.total_points}", f"{metrics.points_completion_pct}%")
    cols[2].metric("Blocked", metrics.blocked, f"{metrics.blocked_points} pts", delta_color="inverse")
    cols[3].metric("Overdue", metrics.overdue_tasks, delta_color="inverse")
    cols[4].metric("Days remaining", metrics.days_remaining)
    st.progress(min(metrics.points_completion_pct / 100, 1.0), text="Story point completion")

    st.subheader("Why the health score is what it is")
    for reason in metrics.health_reasons or ["No penalties applied - the sprint is on plan."]:
        st.write(f"- {reason}")

    st.subheader("Sprint backlog")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ID": t.id,
                    "Title": t.title,
                    "Status": t.status,
                    "Priority": t.priority.value,
                    "Owner": t.owner or "—",
                    "Points": t.story_points,
                    "Due": t.due_date,
                }
                for t in sprint.tasks
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

with blockers_tab:
    items = state.get("blockers", [])
    st.caption(f"{len(items)} blocker(s) detected - explicit, dependency, stale and overdue.")
    if items:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ID": b.task_id,
                        "Title": b.title,
                        "Type": b.kind,
                        "Priority": b.priority.value,
                        "Owner": b.owner,
                        "Days blocked": b.days_blocked,
                        "Points": b.blocking_points,
                        "Carried over": "yes" if b.carried_over else "no",
                        "Reason": b.reason,
                    }
                    for b in items
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("No blockers detected.")

with risks_tab:
    approvals = {a.risk_id: a for a in state.get("approvals", [])}
    for risk in state.get("risks", []):
        decision = approvals.get(risk.id)
        badge = {"approve": "✅ approved", "reject": "🚫 rejected", "revise": "✏️ revised"}.get(
            decision.decision if decision else "", ""
        )
        st.markdown(
            f"**[{risk.severity.value.upper()}] {risk.title}** · `{risk.category}` · "
            f"source: {risk.source} {badge}"
        )
        for item in risk.evidence:
            st.caption(f"• {item}")
        st.write(f"**Recommendation:** {risk.recommendation}")
        if decision and decision.note:
            st.info(f"Reviewer note: {decision.note}")
        st.divider()

with trends_tab:
    st.subheader("Week over week")
    arrow = {"improving": "▲", "declining": "▼", "flat": "▬"}
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Signal": t.label,
                    "Previous": t.previous,
                    "Current": t.current,
                    "Direction": f"{arrow.get(t.direction, '')} {t.direction}",
                }
                for t in state.get("trends", [])
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Recurring / stale issues")
    issues = state.get("recurring_issues", [])
    if issues:
        for issue in issues:
            sprints = ", ".join(str(s) for s in issue.sprints_seen)
            st.write(
                f"- **{issue.task_id} {issue.title}** - sprints {sprints}, "
                f"{issue.days_outstanding} days outstanding. {issue.detail}"
            )
    else:
        st.success("Nothing has carried over between sprints.")

    st.subheader("Memories recalled for this run")
    memories = state.get("memories", [])
    st.caption(f"{len(memories)} memory record(s) retrieved from Mem0 · {state.get('memory_writes', 0)} written back.")
    for record in memories[:12]:
        st.write(f"- `{record.metadata.get('kind', 'note')}` (score {record.score:.2f}) {record.text}")

with report_tab:
    report = state.get("report")
    if report is None:
        st.info("No report generated.")
    else:
        markdown = render_markdown(report)
        st.download_button(
            "⬇ Download report (Markdown)",
            markdown,
            file_name=f"sprintpulse_{sprint.meta.project_id}_sprint_{sprint.meta.number}.md",
            mime="text/markdown",
        )
        st.markdown(markdown)

with memory_tab:
    st.subheader("Ask the project's memory")
    st.caption("Answers come from Mem0 records written by previous runs, plus live tracker figures.")
    st.session_state.setdefault("question", EXAMPLE_QUESTIONS[0])
    cols = st.columns(len(EXAMPLE_QUESTIONS))
    for index, (col, example) in enumerate(zip(cols, EXAMPLE_QUESTIONS)):
        if col.button(example, use_container_width=True, key=f"example_{index}"):
            st.session_state.question = example
            st.rerun()
    question = st.text_input("Question", key="question")
    if st.button("Ask", type="primary", key="ask"):
        with st.spinner("Searching project memory…"):
            answer = answer_question(question, project_id, sprint.meta.number, faults)
        st.markdown(answer.answer)
        st.caption(f"Answer mode: {answer.mode}")
        for note in answer.notes:
            st.caption(f"⚠️ {note}")
        with st.expander(f"Memory records used ({len(answer.sources)})"):
            for record in answer.sources:
                st.write(f"- `{record.metadata.get('kind', 'note')}` (score {record.score:.2f}) {record.text}")

with st.expander("Run diagnostics"):
    st.write(f"**Thread:** `{st.session_state.get('thread_id')}` · **status:** {state.get('status')}")
    st.write("**Nodes executed:** " + " → ".join(result.trace))
    errors = state.get("errors", [])
    if errors:
        for err in errors:
            st.write(f"- **{err.node}** ({err.kind}/{err.severity}, {err.attempts} attempt(s)): {err.message}")
    else:
        st.write("No errors recorded.")
