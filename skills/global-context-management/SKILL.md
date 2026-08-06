---
name: global-context-management
description: "Use for complex Codex work: planning, implementation, debugging, refactoring, migration, architecture, reviews, tests, CI failures, or multi-file tasks. Keep parent context concise with durable task state, targeted read-only subagents when useful, focused validation, and final risk review."
---

# Global Context Management

## Help

For `$global-context-management --help` or `$global-context-management -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Use this skill to keep long or complex Codex work focused and recoverable:
concise parent context, durable task-state notes, bounded read-only subagents
when it is useful, focused validation, and final risk review.

## When To Use

- Multi-file reading, understanding, validation, implementation, refactoring, migration, debugging, or architecture
  work.
- CI failures, long logs, test failures, or unclear root-cause investigations.
- Code review or risk review where many files or contracts may be relevant.
- Tasks likely to run long enough to hit compaction or lose earlier decisions.

## When Not To Use

- Tiny single-file edits need only the lightweight parts: targeted reads,
  minimal task-state notes if already injected, focused validation, and a short
  final summary.
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
- Do not claim or fake subagent use when tools are unavailable or active
  instructions forbid delegation.
- Do not treat skill activation, generic hook context, a complex task, or
  configured `[agents.*]` roles alone as delegation authorization.
- Do not claim runtime hook or skill activation is proven unless it was
  observed in the current Codex surface.

## Inputs

- The current user prompt and active system/developer/repo instructions.
- The durable task-state path injected by hooks, when available.
- An existing task-state file at that path, when prior context may matter.
- Optional same-workspace prior task-state candidate paths injected by hooks for
  complex prompts, treated only as stale hints.
- Optional user-enabled hook-policy context that requests bounded read-only
  delegation for the current prompt.
- Current runtime tool availability, including whether subagent controls are
  visible or discoverable through `tool_search`.

## Required Reads

- Read the injected task-state file at task start, resume, or after compaction
  when it exists and prior decisions may matter.
- Read hook-suggested same-workspace prior task-state candidates only when they
  appear relevant; verify any useful facts against current evidence.
- Read target project files with targeted `rg`, `rg --files`, and small ranges
  before editing.
- When changing this skill or its local setup contract, read `README.md`,
  `references/local-setup.md`, relevant `assets/`, and duplicated
  `config-codex` surfaces.
- When changing Codex-specific guidance, verify current official Codex docs or
  clearly mark unverified runtime behavior.

## Writes

- May create or update only the advertised task-state file under
  `$CODEX_HOME/task-state` when continuity is useful and writes are permitted.
- May edit requested project files, skill source files, docs, tests, templates,
  or validators in the current task scope.
- May update this skill's public-safe local source materials under the Learning
  Loop when the task contract allows source edits.
- Must not write repo-local task-state files, SDLC run state, hook runtime
  files, credentials, raw logs, or unrelated `$CODEX_HOME` files.

## Runtime Boundaries

This skill is a workflow contract, not a context-window control plane. It
cannot remove system/developer instructions, injected memories, available tool
metadata, prior turns, or tool outputs already in the parent thread.

Valid delegation authorization comes from either:

- a current prompt that asks Codex to use or spawn subagents, delegation, or
  parallel agents; or
- a user-enabled local hook policy that injects a bounded read-only delegation
  request for the current prompt, when the runtime accepts that hook context.

After either source authorizes delegation, dynamically choose targeted helper
roles yourself when useful. The user does not need to name `repo_mapper`,
`test_strategist`, or `risk_reviewer`, and the parent should not ask for
another prompt only because the original prompt did not name subagents.

If delegation is authorized and useful but controls are not visible, use
`tool_search` for multi-agent/subagent tools before reporting delegation
unavailable. If tools are unavailable, disabled, or forbidden, continue locally
with narrower reads and state that boundary.

Hooks own only non-SDLC global-context events:

- `SessionStart`: stable workspace context, coding standards, environment
  notes, and task-state path hints.
- `UserPromptSubmit`: small global reminders, prompt safety, lightweight
  context hints, and opt-in delegation requests.

Hooks must not call subagent tools directly, route `sdlc-start`, select SDLC
phase skills, modify SDLC run state, or inject large documents.

## Task State

Use the durable task-state path injected by global hooks. No legacy task-state
path is created when the hook payload has no session id. If no path is
available, continue without durable task state rather than creating a manual or
repo-local fallback.

Normal `SessionStart` only advertises or reuses the path. A `compact`
`SessionStart`, or the first complex `UserPromptSubmit` for the session, creates
an empty private scaffold with `0700` directories and a `0600` `current.md`.
Hooks never copy prompt text into that scaffold. The parent owns all semantic
content and rewrites the rolling summary when the task changes. If a local
PreToolUse write guard is installed, it must allow writes under
`$CODEX_HOME/task-state` while continuing to block unrelated `$CODEX_HOME`
runtime edits such as hook rewrites.

For complex prompts, `UserPromptSubmit` may list a small bounded set of
same-workspace prior `current.md` candidate paths from the same
`$CODEX_HOME/task-state/<workspace>-<hash>/` bucket. The hook must not inject
historical task-state contents. The parent may read a candidate only when it
appears relevant, and must verify it as stale context. The current session's
advertised `current.md` remains the only write target.

At task start, resume, or context transition, read existing task state when it
may contain prior decisions, validation status, or next action. Keep
`current.md` as a rolling summary, not an append-only log: retain only the
objective, constraints, decisions, changed files, validation status, risks, and
next action needed for continuation. Keep it below 12 KiB when practical and
use `assets/task-state-template.md` only when a section template is helpful.

When `troubleshoot` records an active `codex-remediation-budget:v1` marker,
preserve exactly one valid marker during every rolling-summary rewrite until it
is resolved, superseded by a causally different blocker, or replaced by a new
user-authorized tranche. Do not infer attempts, change limits, or copy raw
failure evidence into it; `troubleshoot` owns those semantics. When
`troubleshoot` establishes a causally different blocker, preserve only its
fresh attempt-1 marker and keep the earlier outcome in prose; do not carry the
old blocker's attempts, active time, tranche, exhaustion status, or stop trigger
into the replacement marker.

Update task state after initial exploration, before implementation, after major
edits, after validation, before a long pause or compaction, and before the
final response. If a task-state file grows harder to scan than the current
thread, summarize it before relying on it.

## Idempotency

- Reuse the injected task-state path for the current session; do not invent a
  fallback path.
- Replace or compact task state instead of appending raw logs or duplicating
  stale plans.
- Spawn each useful subagent role only once for a specific sidecar question
  unless a distinct follow-up is needed.
- Track every spawned subagent handle until it is closed or explicitly reported
  as unclosable.
- Re-run validation after edits and record current status instead of preserving
  stale results.

## Parent-Thread Discipline

Keep the parent thread focused on objective, constraints, current plan,
decisions, files changed, verification status, risks, and final answer.

Avoid broad file listings, raw logs, repeated stack traces, abandoned attempts,
broad environment dumps, and large copied documentation blocks unless exact text
is needed for correctness. Prefer `rg -n`, `rg --files`, small `sed` ranges,
exact paths, and output limits.

Route broad read-only mapping, test discovery, and risk review to subagents
when delegation is authorized, useful, available, and permitted. Put durable
decisions, current plan, and validation status in task state; do not copy raw
logs there.

## Subagent Delegation

Use bounded read-only helpers for independent sidecar work when delegation is
authorized, materially useful, and permitted by the current runtime:

- `repo_mapper`: map relevant files, symbols, execution paths, and conventions.
- `test_strategist`: identify focused tests, fixtures, and validation order.
- `risk_reviewer`: review near-final work for correctness, regressions,
  security, compatibility, edge cases, and missing tests.

Do not spawn every configured role by default. Use one or two early helpers
only when their work is independent, reserve `risk_reviewer` for non-trivial or
risky near-final changes, and avoid multiple write-capable agents in one
workspace unless the user explicitly asks for worktrees or parallel
implementation.

Ask helpers for concise final summaries. The parent must wait or close as
appropriate, consolidate what matters, and close every spawned subagent handle
with `close_agent` or equivalent close controls once it is completed or no
longer needed. Completed agents remain open and can count toward concurrency
until closed. Before the final response, perform a final lifecycle sweep over
every spawned handle and report any handle that could not be closed.

## Failure Handling

- If task-state path is unavailable, continue without durable state and do not
  create a manual or repo-local fallback.
- If task state or prior candidates appear stale, treat them as hints and
  verify drifted facts before acting.
- If delegation is authorized and useful but controls are hidden, use
  `tool_search` before reporting delegation unavailable.
- If spawning is denied, unavailable, or forbidden, continue locally with
  narrower reads and report the boundary.
- If subagent close controls are unavailable or closing fails, report the
  residual open or running handle.
- If validation fails, classify whether the failure is from changed source,
  duplicated template drift, environment/runtime availability, or optional
  profile mismatch before retrying.

## Process

1. Understand the task, constraints, and relevant skills.
2. Read current task state and relevant prior candidates when prior context may
   matter; update the current task state if continuity is useful.
3. Explore with targeted reads; delegate independent read-heavy sidecar work
   when authorized, useful, available, and permitted.
4. Plan the smallest coherent implementation.
5. Edit in focused patches using existing project conventions.
6. Inspect the diff and align docs, changelog, templates, and validators when
   changed behavior requires it.
7. Run narrow validation first, then broader checks when appropriate.
8. Use a read-only risk pass for non-trivial or risky changes when authorized
   and useful.
9. Close spawned helpers, update task state, and return the final summary.

## Completion Criteria

- The requested implementation, review, or investigation is complete, or the
  remaining blocker is explicitly reported.
- Parent context contains decisions, changed files, validation status, and
  residual risk rather than raw logs or broad dumps.
- Task state, when available and useful, reflects the current plan, validation
  status, risks, and next action.
- Spawned subagents have returned summaries or been closed as no longer needed;
  no running or completed handle remains open silently.
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

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

For code changes, return:

- summary
- files changed
- verification performed
- remaining risks or follow-ups
