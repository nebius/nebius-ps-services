---
name: global-context-management
description: "Use for complex Codex work: planning, implementation, debugging, refactoring, migration, architecture, reviews, tests, CI failures, or multi-file tasks. Keep parent context concise with durable task state, authorized targeted read-only subagents when useful and permitted, focused validation, and final risk review."
---

# Global Context Management

## Purpose

Use this skill to keep long or complex Codex work focused and recoverable.
The workflow keeps durable decisions outside the conversation and uses bounded
read-only subagents for useful independent sidecar work after delegation is
authorized by the prompt or by a user-enabled local hook policy, when the
current runtime exposes the tools. It keeps the parent thread centered on
implementation and final judgment.

## When To Use

- Multi-file implementation, refactoring, migration, debugging, or architecture
  work.
- CI failures, long logs, test failures, or unclear root-cause investigations.
- Code review or risk review where many files or contracts may be relevant.
- Tasks likely to run long enough to hit compaction or lose earlier decisions.

## When Not To Use

- For tiny single-file edits, apply only the lightweight parts: concise
  exploration, minimal task-state notes if already injected, focused
  validation, and a short final summary.
- For machine-level Codex installation or local file rendering, use
  `config-codex`; this skill owns the runtime work pattern, not setup.
- For Agentic SDLC orchestration, use `sdlc-start` and the relevant `sdlc-*`
  phase skills; this skill may only wrap that work for context management.

## Must Not

- Do not create repo-local task-state files unless the user explicitly asks.
- Do not store raw prompts, secrets, command output, stack traces, customer
  data, private URLs, or environment-specific values in task-state files.
- Do not make subagents responsible for final decisions. The parent agent owns
  consolidation, edits, verification, and the final answer.
- Do not claim or fake subagent use when the current Codex surface does not
  expose subagent tools or active instructions forbid delegation.
- Do not treat skill activation, generic hook context, or configured
  `[agents.*]` roles as delegation authorization. Authorization comes from the
  user prompt or from a user-enabled local hook policy that deliberately
  injects an explicit delegation request for the current prompt. After one of
  those sources authorizes delegation, dynamically choose and spawn targeted
  helper roles yourself when useful; do not ask for another confirmation just
  because the prompt did not name a specific helper role. Check usefulness,
  tool availability, and active runtime policy instead.
- Do not claim runtime hook or skill activation is proven unless it was
  observed in the current Codex surface.

## Inputs

- The current user prompt and active system/developer/repo instructions.
- The durable task-state path injected by hooks, when available.
- An existing task-state file at that path, when it exists and prior context may
  matter.
- Optional user-enabled hook-policy context that requests bounded read-only
  delegation for the current prompt.
- Current runtime tool availability, including whether subagent controls are
  directly visible or discoverable through `tool_search`.

## Required Reads

- Read the injected task-state file at task start, resume, or after compaction
  when it exists and prior decisions may matter.
- Read target project files with targeted `rg`, `rg --files`, and small file
  ranges before editing.
- When changing this skill or its local setup contract, read `README.md`,
  `references/local-setup.md`, relevant `assets/`, and duplicated
  `config-codex` surfaces.
- When changing Codex-specific guidance, verify current official Codex docs or
  clearly mark unverified runtime behavior.

## Writes

- May create or update only the advertised task-state file under
  `$CODEX_HOME/task-state` when continuity is useful and writes are permitted.
- May edit requested project files, skill source files, docs, tests, or
  templates in the current task scope.
- May update this skill's public-safe local source materials under the Learning
  Loop when the task contract allows source edits.
- Must not write repo-local task-state files, SDLC run state, hook runtime files,
  credentials, raw logs, or unrelated `$CODEX_HOME` files.

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
authorization sources. A complex task, this skill's activation, generic hook
context, or configured agent roles are not enough by themselves. For
prompt-based authorization, the prompt must clearly ask Codex to use or spawn
subagents, use delegation, or run parallel agents. For policy-based
authorization, the hook output must explicitly request bounded read-only
delegation for the current prompt and the current runtime must accept that hook
context. Once authorization is present, choose and spawn targeted helper roles
yourself when useful; the user does not need to name `repo_mapper`,
`test_strategist`, or `risk_reviewer`. This is the automatic mode from the
user's perspective: the user enables the local policy once, the hook injects a
per-turn request, and the parent agent makes the dynamic delegation decision.
The hook still does not call subagent tools directly.

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

When this skill is used during an Agentic SDLC task, it remains only the
context-management wrapper around the work. It must not replace `sdlc-start`,
select SDLC phase skills, write SDLC run state, create or modify
`docs/requirements.md` or `docs/design.md`, or enforce SDLC write policy.

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

## Idempotency

- Reuse the injected task-state path for the current session; do not invent a
  fallback path when the hook did not provide one.
- Update task state as a concise replacement or checkpoint, not by appending raw
  logs or duplicating prior plans.
- Spawn each useful subagent role only once for a specific sidecar question
  unless a new, distinct follow-up is needed.
- Re-run validation after edits and record the current status instead of
  preserving stale results.

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
  when delegation is authorized by the prompt or a user-enabled local hook
  policy, the work is useful and independent, and the tools and instructions
  permit it.
- Put durable decisions, current plan, and validation status in task state;
  do not copy raw logs there.

## Subagent Delegation

For complex tasks, actively use bounded read-only subagents for read-heavy
sidecar work when they materially help, delegation is authorized by the prompt
or a user-enabled local hook policy, and the current Codex surface permits
delegation:

- `repo_mapper`: map relevant files, symbols, execution paths, and conventions.
- `test_strategist`: identify focused tests, fixtures, and validation order.
- `risk_reviewer`: review near-final work for correctness, regressions,
  security, compatibility, edge cases, and missing tests.

Do not spawn every configured role by default. With a conservative
`max_threads = 4` budget, use `repo_mapper` and `test_strategist` early only
when their work is useful and independent, close completed helpers after
consolidating their summaries, and reserve `risk_reviewer` for near-final
review of non-trivial or risky changes.

After delegation is authorized, the default decision should be to dynamically
spawn one or two targeted read-only helpers for independent sidecar work. Skip
delegation only when the task is tiny, the next step is blocked on the same
investigation, there is no independent read-heavy work, subagent controls are
unavailable or denied, or active instructions forbid delegation. Do not skip
only because the user did not name the exact role.

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

## Failure Handling

- If the task-state path is unavailable, continue without durable task state and
  do not create a manual or repo-local fallback.
- If task state exists but appears stale, treat it as a hint and verify drifted
  facts before acting on them.
- If delegation is authorized and useful but controls are not visible, use
  `tool_search` to look for multi-agent/subagent tools before reporting
  delegation unavailable.
- If subagent spawning is denied, unavailable, or forbidden by active
  instructions, continue locally with narrower reads and report that boundary.
- If validation fails, classify whether the failure is from changed source,
  duplicated template drift, environment/runtime availability, or optional
  profile mismatch before retrying.

## Process

1. Understand the task and constraints.
2. Read existing global task state when prior context may matter; create the
   task-state file only when complex work needs continuity, then update it.
3. Explore with targeted reads; use read-only subagents when delegation is
   authorized by the prompt or a user-enabled local hook policy, useful for the
   task, available, and permitted. Wait for their final summaries and close
   completed subagent threads after consolidation when close controls are
   available. For multiple subagents, repeat wait-and-close until every spawned
   handle is closed.
4. Plan the smallest coherent implementation.
5. Edit in focused patches using existing project conventions.
6. Inspect the diff.
7. Run narrow validation first, then broader checks when appropriate.
8. Use `risk_reviewer` before finalizing non-trivial or risky changes when
   subagent delegation is authorized by the prompt or a user-enabled local hook
   policy, useful, available, and permitted.
9. Update task state and return the final summary.

## Completion Criteria

- The requested implementation, review, or investigation is complete or the
  remaining blocker is explicitly reported.
- Parent context contains decisions, changed files, validation status, and
  residual risk rather than raw logs or broad dumps.
- Task state, when available and useful, reflects current plan, validation
  status, risks, and next action.
- Any spawned subagents have returned final summaries and been closed when close
  controls are available.
- Relevant docs, README, changelog, duplicated templates, and validators are
  aligned for changed behavior.

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
