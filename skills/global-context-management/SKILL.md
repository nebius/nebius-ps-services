---
name: global-context-management
description: "Use for complex Codex work: planning, implementation, debugging, refactoring, migration, architecture, reviews, tests, CI failures, or multi-file tasks. Keep parent context concise with durable task state, optional authorized read-only subagents, focused validation, and final risk review."
---

# Global Context Management

## Purpose

Use this skill to keep long or complex Codex work focused and recoverable.
The workflow keeps durable decisions outside the conversation, delegates noisy
read-heavy investigation when explicitly authorized, available, and useful, and
keeps the parent thread centered on implementation and final judgment.

## Use This Skill For

- Multi-file implementation, refactoring, migration, debugging, or architecture
  work.
- CI failures, long logs, test failures, or unclear root-cause investigations.
- Code review or risk review where many files or contracts may be relevant.
- Tasks likely to run long enough to hit compaction or lose earlier decisions.

For tiny single-file edits, apply only the lightweight parts: concise
exploration, minimal task-state notes if already injected, focused validation,
and a short final summary.

## Non-Goals

- Do not create repo-local task-state files unless the user explicitly asks.
- Do not store raw prompts, secrets, command output, stack traces, customer
  data, private URLs, or environment-specific values in task-state files.
- Do not make subagents responsible for final decisions. The parent agent owns
  consolidation, edits, verification, and the final answer.
- Do not assume subagent tools are available or permitted just because this
  skill was loaded. Use them only when the current Codex surface exposes them
  and current user or developer instructions allow delegation.
- Do not treat generic hook-injected or skill-injected instructions as user
  authorization when the runtime requires the user to explicitly request
  subagents, delegation, or parallel agents. A local hook policy can record a
  deliberate user opt-in and inject that request, but active runtime policy may
  still deny delegation.
- Do not claim runtime hook or skill activation is proven unless it was
  observed in the current Codex surface.

## Runtime Boundaries

This skill is a workflow contract, not a context-window control plane.

It cannot remove system/developer instructions, injected memories, available
tool metadata, prior conversation turns, or tool outputs that already entered
the parent thread. It can only influence future behavior: concise exploration,
external task-state notes, bounded subagent delegation when explicitly
authorized and available, and careful final consolidation.

If subagent tools are unavailable, disabled, hidden in the current surface, or
not permitted by the current instructions, continue locally with narrower
reads. When delegation is otherwise authorized and useful but subagent controls
are not visible, and `tool_search` is available, first search for
multi-agent/subagent tools before reporting delegation unavailable. State that
delegation was unavailable or not permitted instead of pretending it happened.

If the active runtime policy requires explicit user authorization for
subagents, a complex task or this skill's activation is not enough by itself.
The user prompt must clearly ask Codex to use or spawn subagents, use
delegation, or run parallel agents, unless a user-enabled local hook policy
injects that request and the current runtime accepts it.

## Task State

Use the durable task-state path injected by global hooks. No legacy task-state
path is created when the hook payload has no session id. If no path is
available, continue without durable task state rather than creating a manual or
repo-local fallback.

At the start of a complex task, resume, or context transition, read the
existing task-state file before planning when it may contain prior decisions,
validation status, or next action. Treat task state as a concise continuity
note, not an unquestioned source of truth; verify facts that may have drifted.

Keep task state concise:

```text
# Current Codex task state

## Workspace
## Objective
## Constraints
## Current plan
## Decisions made
## Relevant files and symbols
## Commands run
## Test status
## Risks
## Next action
```

Update task state after initial exploration, before implementation, after major
edits, after validation, before a long pause or compaction, and before the
final response. The file is useful only if it is read on continuation and kept
small enough to act on.

## Parent-Thread Discipline

Keep the parent thread focused on:

- user objective and constraints
- current plan and decisions
- files changed
- verification status
- risks and final answer

Do not paste broad file listings, raw logs, repeated stack traces, abandoned
approaches, broad environment dumps, or large copied documentation blocks
unless the exact text is needed for correctness.

Before running a noisy command in the parent thread, narrow it:

- Prefer `rg -n`, `rg --files`, small `sed` ranges, and exact paths over broad
  recursive reads.
- Limit command output when possible and summarize only the relevant facts.
- Route broad read-only mapping, test discovery, and risk review to subagents
  only when the user explicitly authorized delegation and the tools and
  instructions permit it.
- Put durable decisions, current plan, and validation status in task state;
  do not copy raw logs there.

## Subagent Delegation

For complex tasks, use bounded read-only subagents when they materially help,
the user explicitly authorized delegation if required by the runtime, and the
current Codex surface permits delegation:

- `repo_mapper`: map relevant files, symbols, execution paths, and conventions.
- `test_strategist`: identify focused tests, fixtures, and validation order.
- `risk_reviewer`: review near-final work for correctness, regressions,
  security, compatibility, edge cases, and missing tests.

Do not spawn every configured role by default. With a conservative
`max_threads = 4` budget, use `repo_mapper` and `test_strategist` early only
when their work is useful and independent, close completed helpers after
consolidating their summaries, and reserve `risk_reviewer` for near-final
review of non-trivial or risky changes.

Ask subagents for concise final summaries only, and tell them to stop after
returning the result instead of waiting for follow-up prompts. The parent
agent owns the lifecycle: spawn bounded read-only helpers, wait for their final
results, consolidate what matters, and close completed subagent threads when
the runtime exposes close controls and no follow-up is needed.
When multiple subagents are running, close each completed handle as soon as its
terminal result is received, whether that result arrives from `wait_agent` or
from an asynchronous completion notification. Repeat wait-and-close until every
spawned subagent has been closed.

Do not use multiple write-capable agents in the same workspace unless the user
explicitly asks for worktrees or parallel implementation.

Good delegation targets are read-heavy sidecar tasks that can run while the
parent keeps moving. Keep immediate blockers in the parent thread when waiting
for a subagent would stall the next step.

## Workflow

1. Understand the task and constraints.
2. Read existing global task state when prior context may matter; create the
   task-state file only when complex work needs continuity, then update it.
3. Explore with targeted reads; use read-only subagents only when they are
   explicitly authorized, useful, available, and permitted. Wait for their
   final summaries and close completed subagent threads after consolidation
   when close controls are available. For multiple subagents, repeat
   wait-and-close until every spawned handle is closed.
4. Plan the smallest coherent implementation.
5. Edit in focused patches using existing project conventions.
6. Inspect the diff.
7. Run narrow validation first, then broader checks when appropriate.
8. Use `risk_reviewer` before finalizing non-trivial or risky changes only
   when subagent delegation is explicitly authorized and permitted.
9. Update task state and return the final summary.

## Local Setup

For machine-level installation, use `config-codex` or read
`references/local-setup.md`. This skill owns the complex-task workflow; the
setup skill owns rendering and patching a local Codex home. Keep local runtime
files under `$CODEX_HOME`; keep this skill public, generic, and free of
personal paths or secrets. When script execution is permitted, use
`scripts/validate-local-templates.py` for a local-only template smoke test when
validating hook setup. For the human-facing design and architecture map, read
`README.md`.

## Output Contract

For code changes, return:

- summary
- files changed
- verification performed
- remaining risks or follow-ups
