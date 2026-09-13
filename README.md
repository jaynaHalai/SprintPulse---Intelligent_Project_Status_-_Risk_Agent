# SprintPulse

**Intelligent project status & risk agent** — a stateful LangGraph agent that reads sprint data from a
project-management tool, works out what is actually going wrong, remembers it, asks a human before it
escalates, and writes the weekly status report.

---

## Problem

Weekly status reporting is manual, slow and forgetful. A delivery lead exports the sprint board, works out
what moved, tries to remember which of these blockers they also wrote about last week, guesses at what to
escalate, and rewrites the same report from scratch every Friday. Two failure modes follow:

1. **No memory.** Nobody notices that a task has been blocked for three weeks, because each report is
   written in isolation.
2. **No judgement trail.** Escalations either happen by gut feel, or get buried under "in progress".

A one-shot LLM summariser does not fix this. It reads one export, invents a cheerful paragraph, has no idea
what happened last sprint, and escalates nothing.

## Solution

SprintPulse runs a multi-step, stateful workflow over sprint data:

- pulls the current and previous sprint through a **replaceable tool interface** (JSON today, Jira/Asana/Notion tomorrow);
- computes progress, a transparent **health score**, and **blockers the tracker never labels** (dependency,
  stale, overdue) using deterministic analytics;
- **recalls what it learned in earlier runs** from Mem0 and compares week over week;
- rates risks with an LLM grounded in those verified facts, with a rule-based safety net;
- **stops and asks a human** before escalating anything serious — and can take the reviewer's note back
  through risk assessment for a second pass;
- writes a professional weekly report and **stores this sprint's findings back into memory**;
- answers historical questions such as *"What has been stuck for more than one sprint?"* from memory.

A sample of the generated output is in [`docs/sample_weekly_report.md`](docs/sample_weekly_report.md).

---

## Architecture

```
START
  │
  └─▶ ingest_project_data ──(sprint missing / malformed)──▶ END (run halted, reason reported)
          │
          ▼
      analyze_sprint ─▶ detect_blockers ─▶ recall_memory ─▶ trend_analysis ─▶ assess_risk
                                                                                  │
                        ┌──────────(no risk needs a decision)──────────────────────┘
                        │                                                          │
                        │                                            (escalating risk found)
                        │                                                          ▼
                        │                                                  request_approval
                        │                                                  ⏸ interrupt() — waits for a human
                        │                                                          │
                        │                          ┌──"revise" + note (max once)───┘
                        │                          ▼                                │
                        │                     assess_risk                  "approve" / "reject"
                        │                                                           │
                        └──────────────────────▶ compose_report ◀────────────────────┘
                                                      │
                                                      ▼
                                                persist_memory ─▶ END
```

Everything the nodes share lives in one explicit state object (`state.PulseState`, a `TypedDict`):
sprint data, previous sprint, metrics, previous metrics, blockers, recalled memories, trends, recurring
issues, risks, escalations, human approvals, reviewer context, revision count, the report, memory write
count, plus operational fields (`errors`, `degraded`, `status`, `halt_reason`). Domain objects are Pydantic
models, so the same shapes are used for LLM structured output, for the checkpointer and for the UI.

Two edges are genuinely conditional, not decoration:

| Edge | Decision |
|---|---|
| after `ingest_project_data` | critical data failure halts the run; otherwise continue |
| after `assess_risk` | interrupt for a human **only** when a risk is high/critical or flagged for escalation — a healthy sprint is reported without ever pausing |
| after `request_approval` | a reviewer note routes back into `assess_risk` for one revision pass, then on to the report |

### Project structure

```
sprintpulse/
├── app.py                  Streamlit UI (run, review, report, ask memory)
├── graph.py                LangGraph wiring + SprintPulseAgent façade (start / resume)
├── state.py                PulseState + every domain model
├── analytics.py            Deterministic metrics, blockers, trends, recurring issues, fallback risks
├── llm.py                  Model factory, structured output, retries, offline detection
├── qa.py                   Historical Q&A agent over memory + tracker figures
├── report_render.py        Markdown rendering of the weekly report
├── resilience.py           Retry helper
├── config.py               Environment-driven settings (LLM, Mem0, thresholds)
├── nodes/                  One module per stage of the graph
│   ├── ingest.py  analysis.py  memory_nodes.py  trends.py  risk.py  approval.py  report.py
├── tools/
│   ├── project_data.py     ProjectDataSource interface + JSON implementation
│   └── memory.py           Mem0-backed ProjectMemory with a degraded local fallback
├── data/                   sprint_4.json, sprint_5.json, sprint_6_malformed.json
├── docs/sample_weekly_report.md
└── tests/                  44 tests: analytics, data source, memory, graph, UI
```

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | **LangGraph** 1.2 (`StateGraph`, conditional edges, `interrupt()`, checkpointer) |
| LLM integration | **LangChain** 1.4 (`init_chat_model`, `with_structured_output`) |
| Model | **Claude Opus 5** by default (`SPRINTPULSE_LLM_PROVIDER`/`_MODEL` configurable; OpenAI supported) |
| Memory | **Mem0** (`mem0ai` 2.0) — hosted platform client or self-hosted with a local Qdrant store |
| Embeddings | **fastembed** (`BAAI/bge-small-en-v1.5`) locally, so memory needs no API key |
| UI | **Streamlit** |
| Config | **python-dotenv** |

## Agent flow, node by node

| Node | What it does | Failure behaviour |
|---|---|---|
| `ingest_project_data` | Calls `get_current_sprint()` and `get_previous_sprint()` on the tool interface. Retries transient I/O twice; structural errors fail fast. | **Critical** — the run halts with a reason. A missing previous sprint only degrades the comparison. |
| `analyze_sprint` | Progress, story points, overdue/unowned counts, days remaining, and a health score (0–100 → healthy / watch / at_risk / critical) that always lists the reasons behind it. Also scores the previous sprint. | Deterministic; cannot fail on valid data. |
| `detect_blockers` | Explicit blockers **plus** implicit ones: unfinished dependencies, in-progress tasks with no update for N days, overdue work. Marks anything carried over from last sprint. | Deterministic. |
| `recall_memory` | Four targeted Mem0 searches (blockers, unresolved risks, health history, carry-overs), de-duplicated. | **Degraded** — falls back to a local store and says so. |
| `trend_analysis` | Week-over-week signals (completion, points, blocked count/points, overdue, health score) and recurring issues from data *and* memory. | Deterministic. |
| `assess_risk` | LLM structured output (`RiskAssessment`) grounded strictly in the computed facts, memories and any reviewer note. Retried twice. | **Degraded** — falls back to the rule-based risk register; a guardrail also re-adds any *critical* deterministic risk the model omitted. |
| `request_approval` | Builds the escalation list and calls `interrupt()`. The graph stops until the caller resumes with decisions. | Skipped entirely when nothing needs a decision. |
| `compose_report` | LLM narrative (`ReportNarrative`) + deterministic figures → `WeeklyReport`. Approved actions are committed, rejected ones are recorded but never actioned. | **Degraded** — a template narrative is used instead. |
| `persist_memory` | Writes health, blockers, risks (with the human's verdict), recurring issues and reviewer notes back to Mem0, skipping anything already stored. | **Degraded** — the report still stands; the gap is reported. |

## Memory

Memory is a real Mem0 read/write, not "load last week's JSON into the prompt".

- **Scope:** every record is written under `user_id="project::<project_id>"`, so projects never see each
  other's history (there is a test for this).
- **Written after every run:** sprint health and score, each blocker with its reason and age, each risk with
  its severity, recommendation and the reviewer's verdict, each recurring issue, and any note a reviewer typed.
- **Read before analysis:** `recall_memory` runs four targeted searches so the risk assessment sees what the
  agent knew in previous sprints.
- **Backends:** `MEM0_MODE=oss` (default) uses `mem0.Memory` with a local on-disk Qdrant collection per
  project and local fastembed embeddings — **no API key required**. `MEM0_MODE=platform` uses
  `mem0.MemoryClient` with `MEM0_API_KEY`. Mem0 fact extraction (`infer`) is enabled automatically when an
  LLM key is present; without one, entries are stored verbatim.
- **Historical questions** (the "Ask memory" tab) search Mem0, add exact carry-over figures from the tracker
  tool, and let the model answer from those two sources only. Without a model, the answer is composed
  deterministically from the same material — so *"What has been stuck for more than one sprint?"* is
  answerable either way.

## Tools

`tools/project_data.py` defines the tool boundary the graph depends on:

```python
class ProjectDataSource(ABC):
    def list_sprints(project_id) -> list[int]
    def get_sprint(project_id, sprint_number) -> Sprint
    def get_current_sprint(project_id, sprint_number=None) -> Sprint
    def get_previous_sprint(project_id, sprint_number) -> Sprint | None
    def get_project_tasks(project_id, sprint_number=None) -> list[Task]
    def describe_sprints(project_id) -> list[SprintSummary]   # catalogue, flags broken files
```

`JSONProjectDataSource` reads `data/sprint_<n>.json` and validates every task through Pydantic. To go live
against Jira, implement the same class against the Jira REST API and change one factory
(`nodes/common.data_source_for`) — no node, no prompt and no test of the analysis logic has to change.
The datasets are deliberately *not* pre-analysed: they contain raw statuses, priorities, owners, due dates,
dependencies and notes, and every conclusion in the report is derived at run time.

`tools/memory.py` is the second tool: project-scoped Mem0 access with the same swap-friendly shape.

## Human-in-the-loop

The agent is autonomous for data collection, analysis, trend detection and drafting. It stops exactly where
a machine should not decide alone: **before escalating a risk to people outside the team.**

`request_approval` calls LangGraph's `interrupt()` with the escalating risks. The UI renders each one with
its evidence and proposed action, and the reviewer chooses:

- **Approve** → the recommendation enters the report as a committed action (`[approved] …`) and is recorded
  in memory as approved.
- **Reject** → the action is *not* recommended; the report's "Needs human attention" section records that a
  human rejected it, with their reason.
- **Revise + note** → the note goes into state as `human_context` and the graph **routes back into
  `assess_risk`**, which re-rates the risks with the reviewer's information in the prompt. The budget is one
  revision, so the loop cannot spin.

Every decision is stored in state (`approvals`), survives in the checkpointer, shapes the final report, and
is written to memory so next week's run knows what the human already decided.

## Error handling

| Failure | Handling |
|---|---|
| Missing sprint / unknown project | `SprintNotFound` → run halts with the reason shown in the UI |
| Malformed data (bad JSON, missing fields, invalid status, no tasks) | `MalformedSprintData` naming the offending file and field → run halts; the sprint picker flags broken files up front |
| Transient tool I/O | Retried (2 attempts) before being treated as fatal |
| Previous sprint unavailable | Non-critical: the run continues with a baseline trend and a degraded note |
| LLM unavailable, erroring, or returning an empty register | Retried twice, then deterministic fallback for both risks and narrative; a critical-risk guardrail still applies |
| Mem0 unavailable | Falls back to a local keyword store; the UI warns that memory is degraded |
| Memory write failure | The finished report is never lost; the gap is reported |

Nothing is swallowed: every failure appears in `state["errors"]` with node, kind, severity and attempt
count, and user-visible degradation is listed in `state["degraded"]` and in the report itself.

**Demo the failure paths** from the sidebar: *Project data source offline* (critical halt), *LLM provider
offline* (rule-based fallback), *Mem0 offline* (local fallback). Selecting **Sprint 6** demonstrates a real
malformed-data halt using `data/sprint_6_malformed.json`.

## Setup

Requires Python 3.10+ (developed on 3.12).

```bash
git clone <your-repo-url> sprintpulse && cd sprintpulse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # optional - see below
```

### Environment variables

**None are required.** With an empty `.env`, SprintPulse runs end to end: real Mem0 memory (local
embeddings), deterministic risk register and a template narrative.

| Variable | Purpose | Default |
|---|---|---|
| `SPRINTPULSE_LLM_PROVIDER` | `anthropic` or `openai` | `anthropic` |
| `SPRINTPULSE_LLM_MODEL` | Model id | `claude-opus-5` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Enables LLM risk analysis, report narrative and memory Q&A | unset → deterministic mode |
| `SPRINTPULSE_LLM_MAX_TOKENS` | Output cap | `8000` |
| `SPRINTPULSE_LLM_TEMPERATURE` | Only set for models that accept sampling parameters | unset |
| `MEM0_MODE` | `oss` (local Qdrant) or `platform` | `oss` |
| `MEM0_API_KEY` | Hosted Mem0 key (`platform` mode) | unset |
| `MEM0_VECTOR_PATH` / `MEM0_COLLECTION` | Where local memory lives | `.mem0_store` / `sprintpulse` |
| `MEM0_EMBEDDER_PROVIDER` / `_MODEL` / `MEM0_EMBEDDING_DIMS` | Embedding backend | `fastembed` / `BAAI/bge-small-en-v1.5` / `384` |
| `MEM0_INFER` | Mem0 LLM fact extraction | on when an LLM key exists |
| `SPRINTPULSE_DATA_DIR` | Sprint data location | `data` |
| `SPRINTPULSE_STALE_DAYS` | Days without an update before in-progress work counts as stale | `5` |

> The first run downloads the ~90 MB fastembed model; after that memory works offline.

## Run

```bash
streamlit run app.py
```

Then open http://localhost:8501.

Tests:

```bash
pytest -q          # 44 tests: analytics, data source, Mem0 round trip, graph, Streamlit UI
```

## Demo scenario (under 5 minutes)

1. **Seed the history** — sidebar → *Seed memory from earlier sprints*. Sprint 4 runs end to end and writes
   its findings to Mem0. *(~20s)*
2. **Run Sprint 5** — sidebar → *Run status analysis*. Point out the node trace:
   `ingest → analyze → blockers → recall_memory → trends → assess_risk`. *(~30s)*
3. **Human review** — the run has paused. Show the escalations, the evidence behind them, and approve the
   NorthBank gateway escalation while rejecting one with a reason. *(~60s)*
4. **Overview & Blockers** — health `critical` (42/100) with the reasons listed; ATL-204 blocked 18 days,
   plus dependency/stale/overdue blockers the board never labelled. *(~45s)*
5. **Trends & memory** — Sprint 4 → Sprint 5 decline, three issues carried across both sprints, and the
   memories recalled from the earlier run. *(~45s)*
6. **Weekly report** — the generated report, including the rejected escalation recorded under "Needs human
   attention". *(~30s)*
7. **Ask memory** — *"What has been stuck for more than one sprint?"* → ATL-204 (18 days), ATL-205 and
   ATL-208 (25 days). *(~30s)*
8. **Failure path** — tick *Project data source offline* and re-run: the graph halts cleanly with the reason
   and the retry count; or pick **Sprint 6** for a real malformed-data halt. *(~30s)*

## Future improvements

- **Real Jira/Asana/Notion adapter** behind the existing `ProjectDataSource` interface (the only change needed).
- **Slack delivery**: post the approved report to a channel and collect approvals from Slack instead of the UI.
- **Scheduled runs**: a weekly cron that runs every project and only pings a human when something escalates.
- **Durable checkpointing** (`SqliteSaver`/Postgres) so a paused review survives a restart.
- **Richer analytics**: velocity trends across many sprints, burndown projection, per-owner load, cycle time.
- **Evaluation harness**: a labelled set of sprints with expected blockers/risks to measure end-to-end
  accuracy as prompts and thresholds change.
