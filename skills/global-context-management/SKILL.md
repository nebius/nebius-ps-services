---
name: global-context-management
description: "Use for complex Codex work: planning, implementation, debugging, refactoring, migration, architecture, reviews, tests, CI failures, or multi-file tasks. Keep parent context concise with durable task state, authorized read-only subagents when useful and permitted, focused validation, and final risk review."
---

# Global Context Management

## Purpose

Use this skill to keep long or complex Codex work focused and recoverable.
The workflow keeps durable decisions outside the conversation, delegates noisy
read-heavy investigation when authorized by the prompt or by a user-enabled
local hook policy, useful for the task, and available in the current runtime.
It keeps the parent thread centered on implementation and final judgment.

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
- Do not treat skill activation, generic hook context, or configured
  `[agents.*]` roles as delegation authorization. Authorization comes from the
  user prompt or from a user-enabled local hook policy that deliberately
  injects a lightweight delegation request for the current prompt; active runtime
  policy may still deny delegation.
- Do not claim runtime hook or skill activation is proven unless it was
  observed in the current Codex surface.

## Runtime Boundaries

This skill is a workflow contract, not a context-window control plane.

It cannot remove system/developer instructions, injected memories, available
tool metadata, prior conversation turns, or tool outputs that already entered
the parent thread. It can only influence future behavior: concise exploration,
external task-state notes, bounded subagent delegation when authorization is
present and the tools are available, and careful final consolidation.

If subagent tools are unavailable, disabled, hidden in the current surface, or
not permitted by the current instructions, continue locally with narrower
reads. When delegation is otherwise authorized and useful but subagent controls
are not visible, and `tool_search` is available, first search for
multi-agent/subagent tools before reporting delegation unavailable. State that
delegation was unavailable or not permitted instead of pretending it happened.

Treat the user prompt and a user-enabled local hook policy as the two valid
authorization sources. A complex task, this skill's activation, or configured
agent roles are not enough by themselves. The prompt must clearly ask Codex to
use or spawn subagents, use delegation, or run parallel agents, unless a
user-enabled local hook policy injects a delegation request and the current runtime
accepts it.

This skill's hook layer owns only non-SDLC global-context events:
`SessionStart` for stable context and task-state path injection, and
`UserPromptSubmit` for lightweight prompt-time context, safety, or opt-in
delegation requests. Agentic SDLC guardrails are separate `PreToolUse` and `Stop`
hooks.

- `SessionStart`: use for global conventions, workspace context, environment
  notes, coding standards, and stable task-state path hints. Do not select
  SDLC phases, modify run state, or inject large documents.
- `UserPromptSubmit`: use only for small global reminders, prompt safety, and
  lightweight context hints. Do not route `sdlc-start`, parse requirements,
  select workflow skills, create run state, or inject large documents.

## Task State

Use the durable task-state path injected by global hooks. No legacy task-state
path is created when the hook payload has no session id. If no path is
available, continue without durable task state rather than creating a manual or
repo-local fallback.

The hook only advertises or reuses the path; the parent agent is responsible
for creating and updating the file when continuity is useful. If a local
PreToolUse write guard is installed, it must allow writes under
`$CODEX_HOME/task-state` while continuing to block unrelated `$CODEX_HOME`
runtime edits such as hook rewrites.

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
  only when delegation is authorized by the prompt or a user-enabled local
  hook policy, and the tools and instructions permit it.
- Put durable decisions, current plan, and validation status in task state;
  do not copy raw logs there.

## Subagent Delegation

For complex tasks, prefer bounded read-only subagents for read-heavy sidecar
work when they materially help,
delegation is authorized by the prompt or a user-enabled local hook policy,
and the current Codex surface permits delegation:

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
3. Explore with targeted reads; use read-only subagents only when delegation
   is authorized by the prompt or a user-enabled local hook policy, useful for
   the task, available, and permitted. Wait for their final summaries and
   close completed subagent threads after consolidation when close controls
   are available. For multiple subagents, repeat wait-and-close until every
   spawned handle is closed.
4. Plan the smallest coherent implementation.
5. Edit in focused patches using existing project conventions.
6. Inspect the diff.
7. Run narrow validation first, then broader checks when appropriate.
8. Use `risk_reviewer` before finalizing non-trivial or risky changes only
   when subagent delegation is authorized by the prompt or a user-enabled local
   hook policy, and permitted.
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

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Output Contract

For code changes, return:

- summary
- files changed
- verification performed
- remaining risks or follow-ups
