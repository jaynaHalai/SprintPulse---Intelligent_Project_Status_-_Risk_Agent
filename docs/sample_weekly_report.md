# Weekly Status Report - Atlas Payments Platform
**Sprint 5 - Payments Go-Live Prep** · 2026-09-01 to 2026-09-14 (as of 2026-09-12) · generated 2026-09-13 16:22

## Overall health: 🔴 Critical
**Sprint 5 - Payments Go-Live Prep is critical with 4/13 tasks complete and 1 blocked.**

21.3% of 61 story points are delivered with 2 day(s) left in the sprint. The most pressing blocker is ATL-204 (Payment gateway integration (NorthBank)): NorthBank has still not issued sandbox credentials; integration cannot be tested end to end. Blocked since 2026-08-25, before this sprint started. Past its due date of 2026-09-10. Health score is 42/100.

## Sprint progress
- **Tasks complete:** 4/13 (30.8%)
- **Story points:** 13/61 (21.3%)
- **In progress:** 5
- **Not started:** 3
- **Blocked:** 1 task(s), 8 points
- **Overdue:** 4
- **Days remaining:** 2
- **Health score:** 42/100

## Key wins
- ATL-207 Load testing harness - carried over from Sprint 4 and closed.
- ATL-209 Settlement reconciliation job - unblocked and delivered (5 pts).

## Blockers
- **ATL-204 Payment gateway integration (NorthBank)** (explicit, 18 days, owner Marcus Bell) - NorthBank has still not issued sandbox credentials; integration cannot be tested end to end. Blocked since 2026-08-25, before this sprint started. Past its due date of 2026-09-10.
- **ATL-218 Go-live runbook and rollback plan** (dependency, owner Priya Raman) - Waiting on unfinished dependency: ATL-204 (blocked).
- **ATL-205 Fraud scoring model v1** (stale, owner Ife Adeyemi) - In progress with no update since 2026-09-02 (10 days). Past its due date of 2026-09-09.
- **ATL-208 PCI compliance checklist** (overdue, owner Dana Osei) - Past its due date of 2026-09-09.
- **ATL-211 Apple Pay wallet support** (dependency, owner Priya Raman) - Waiting on unfinished dependency: ATL-204 (blocked).
- **ATL-214 Fraud scoring model v2 rollout** (dependency, owner Ife Adeyemi) - Waiting on unfinished dependency: ATL-205 (in_progress).
- **ATL-215 Vendor failover runbook** (stale, owner unassigned) - In progress with no update since 2026-09-06 (6 days).
- **ATL-212 Chargeback dispute UI** (overdue, owner Priya Raman) - Past its due date of 2026-09-10.

## Risks
- **[CRITICAL] Payment gateway integration (NorthBank) blocked for 18 days** (dependency)
  - evidence: NorthBank has still not issued sandbox credentials; integration cannot be tested end to end. Blocked since 2026-08-25, before this sprint started. Past its due date of 2026-09-10.
  - evidence: 8 story points held up.
  - recommended: Escalate ATL-204 to the accountable owner and agree a resolution date before 2026-09-14.
- **[CRITICAL] Sprint 5 is tracking behind plan** (delivery)
  - evidence: 21.3% of story points delivered against 84.6% of the sprint elapsed
  - evidence: ATL-204 (critical) is blocked: NorthBank has still not issued sandbox credentials; integration cannot be tested end to end.
  - evidence: 4 task(s) past their due date
  - evidence: 2 in-progress task(s) with no update for 5+ days
  - recommended: Cut scope to the 6 open items most tied to the sprint goal and confirm the end date with stakeholders.
- **[HIGH] Fraud scoring model v1 has been open across sprints 4, 5** (delivery)
  - evidence: Still 'in_progress' after being 'in_progress' in Sprint 4.
  - evidence: Outstanding for 25 days.
  - recommended: Re-scope, re-assign or explicitly defer ATL-205 (Fraud scoring model v1) at the next planning session.
- **[HIGH] PCI compliance checklist has been open across sprints 4, 5** (delivery)
  - evidence: Still 'in_progress' after being 'todo' in Sprint 4.
  - evidence: Outstanding for 25 days.
  - recommended: Re-scope, re-assign or explicitly defer ATL-208 (PCI compliance checklist) at the next planning session.
- **[MEDIUM] 1 open task(s) have no owner** (resourcing)
  - evidence: ATL-215 Vendor failover runbook
  - recommended: Assign an owner for every open task before the next standup.

## Week-over-week trends
- ▼ **Task completion**: 50.0% → 30.8% - Task completion down 19.2% versus Sprint 4.
- ▼ **Story point completion**: 38.3% → 21.3% - Story point completion down 17.0% versus Sprint 4.
- ▲ **Blocked tasks**: 2 → 1 - Blocked tasks down 1 versus Sprint 4.
- ▲ **Blocked story points**: 13 → 8 - Blocked story points down 5 versus Sprint 4.
- ▼ **Overdue tasks**: 2 → 4 - Overdue tasks up 2 versus Sprint 4.
- ▼ **Health score**: 46 → 42 - Health score down 4 versus Sprint 4.

## Recurring and stale issues
- **ATL-205 Fraud scoring model v1** - open across sprints 4, 5, 25 days outstanding. Still 'in_progress' after being 'in_progress' in Sprint 4.
- **ATL-208 PCI compliance checklist** - open across sprints 4, 5, 25 days outstanding. Still 'in_progress' after being 'todo' in Sprint 4.
- **ATL-204 Payment gateway integration (NorthBank)** - open across sprints 4, 5, 18 days outstanding. Still 'blocked' after being 'blocked' in Sprint 4.

## Recommended actions
1. [approved] Escalate ATL-204 to the accountable owner and agree a resolution date before 2026-09-14.
2. [approved] Re-scope, re-assign or explicitly defer ATL-205 (Fraud scoring model v1) at the next planning session.
3. [approved] Re-scope, re-assign or explicitly defer ATL-208 (PCI compliance checklist) at the next planning session.
4. Assign an owner for every open task before the next standup.

## Needs human attention
- Payment gateway integration (NorthBank) blocked for 18 days - escalation approved by reviewer.
- Fraud scoring model v1 has been open across sprints 4, 5 - escalation approved by reviewer.
- PCI compliance checklist has been open across sprints 4, 5 - escalation approved by reviewer.
- Sprint 5 is tracking behind plan - escalation rejected by reviewer: Delivery slippage is already tracked at the programme level; no separate escalation needed.
- 1 open task(s) still have no named owner.
- ATL-205 has been outstanding 25 days across 2 sprints - decide to escalate, re-scope or drop it.
- ATL-208 has been outstanding 25 days across 2 sprints - decide to escalate, re-scope or drop it.
- ATL-204 has been outstanding 18 days across 2 sprints - decide to escalate, re-scope or drop it.

## Run notes
- Risks were derived from deterministic rules because the LLM was unavailable.
- Report narrative written from the deterministic template (LLM unavailable).

_Narrative source: template._