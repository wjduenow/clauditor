---
ticket: audit-quality-2026-04
title: Code Quality Audit (April 2026)
phase: devolved
worktree: ../worktrees/clauditor/audit-quality
branch: audit/code-quality-2026-04
sessions: 1
last_session: 2026-04-16
beads_epic: clauditor-24h
---

# Audit: Code Quality, Maintainability, Testability, Architecture, UX

This is an **audit plan**, not a feature plan. Output is a sorted list of
improvement suggestions (highest impact first), grouped by category and
sized so each could become a Ralph story if the user wants to devolve
selected findings into beads.

## Discovery

- 22 source modules, ~10.4K LOC; 27 test modules, ~21.5K LOC (2:1 ratio)
- 80% coverage gate enforced; current aggregate ~96%
- Hot spots: `cli.py` (2962), `suggest.py` (1083), `quality_grader.py` (929)
- Six parallel reviews dispatched: architecture, testing, complexity,
  error-handling, CLI/UX, deps/security
- Established `.claude/rules/` library (16 rules) was used as the
  conformance baseline — several rules are violated by current code

## Sorted Findings

(See "Sorted Suggestions" presented to user — same content; this file is
the persistent record. If the user devolves, each P0/P1 entry becomes a
US-### story.)

## Beads Manifest

Epic: **clauditor-24h** — `audit-quality-2026-04: Code Quality Audit (P0+P1)`

| Story | ID | Priority | Dependencies |
|---|---|---|---|
| US-001 — restore or remove broken build hook | clauditor-24h.1 | P0 | (none) |
| US-002 — wrap FileNotFoundError + actionable msg | clauditor-24h.2 | P0 | (none) |
| US-003 — centralized Anthropic SDK helper | clauditor-24h.3 | P0 | (none) |
| US-004 — assertion dispatch dict | clauditor-24h.4 | P1 | (none) |
| US-005 — pure-compute-vs-IO split graders | clauditor-24h.5 | P1 | US-003 |
| US-006 — SkillResult / SkillAsserter split | clauditor-24h.6 | P1 | (none) |
| US-007 — subprocess cleanup error handling | clauditor-24h.7 | P1 | (none) |
| US-008 — cli/ skeleton + small commands | clauditor-24h.8 | P1 | US-002 |
| US-009 — extract grade command | clauditor-24h.9 | P1 | US-008 |
| US-010 — extract remaining commands | clauditor-24h.10 | P1 | US-009 |
| US-011 — refactor test_cli.py | clauditor-24h.11 | P1 | US-010 |
| Quality Gate — review x4 + CodeRabbit | clauditor-24h.12 | P0 | US-001..US-011 |
| Patterns & Memory — update rules/docs | clauditor-24h.13 | P1 | Quality Gate |

Ready queue at devolve time: 6 unblocked stories
(US-001, US-002, US-003, US-004, US-006, US-007) + epic.

## Sessions

- 2026-04-16: Initial audit by 6 parallel reviewers; synthesized into
  sorted suggestions; user picked option A (devolve P0+P1); created
  epic clauditor-24h with 11 stories + Quality Gate + Patterns &
  Memory; committed to dolt. Ready for `/ralph-run` or manual work.
