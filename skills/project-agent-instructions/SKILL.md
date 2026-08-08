---
name: project-agent-instructions
description: "Use only when explicitly routed by Task Implementer or Agentic SDLC after that workflow has issued a validation receipt for current requirements and design; conditionally create, refresh, adopt, or retire a concise selected-project AGENTS.md with deterministic rules, explicit ownership, and fail-closed recovery."
---

# Project Agent Instructions

## Help

For `$project-agent-instructions --help` or `$project-agent-instructions -h`,
return concise help and stop before any workflow step. State the purpose and
invocation policy. Show exact usage for every public action. Describe each
public action, positional argument, and flag in one concise line, including
`-h, --help`; say "No additional public flags" when there are no others. Use
only the documented public interface. For internal or coordinator-only skills,
state that boundary and that no standalone public workflow action exists.
After the selected `SKILL.md` is loaded, help is report-only: do not call any
additional tools, inspect project state, or modify files, private state, Git,
or external systems. Never expose private helper actions or flags or treat
help as workflow authorization.

## Purpose

Maintain the smallest durable, project-specific agent contract that should
apply to every future session in one exact selected project. A valid decision
may be `not-needed`; requirements and design do not automatically justify a
file.

Generated repository content is portable. Personal global instructions are
checked for conflicts but never copied into, or used to suppress otherwise
necessary rules from, the project file. Ancestor project instructions do count
when determining whether a project-specific rule is redundant.

## Invocation Policy

Use only when Task Implementer or `sdlc-start` explicitly routes here. Keep
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Do not expose
a standalone public workflow command.

## When To Use

- Task Implementer has issued a current managed-spec receipt and routes the
  selected project here before locking its coordinator contract.
- Agentic SDLC has issued a current spec-validation receipt and routes here
  before auto-steering, planning, or execution.
- Specs, selected-project identity, relevant evidence, effective Codex config,
  ancestor instructions, renderer, target, or prior decision changed.

## When Not To Use

- Do not invoke directly outside either coordinator.
- Do not run from marker checks or unvalidated specification files.
- Do not create generic guidance, task state, architecture prose, reusable
  procedures, or recursive instruction files.
- Do not create `AGENTS.override.md`.

## Inputs

- Exact selected project root, enclosing Git root, and spec owner.
- Current `docs/requirements.md` and `docs/design.md`.
- Owner-issued mode-`0600` spec-validation receipt in a caller-owned private
  mode-`0700` directory outside Git.
- Active Codex profile and discovery-relevant CLI overrides, encoded in the
  required private runtime-config declaration. Use explicit `null` and `{}`
  values when neither applies.
- Applicable global, ancestor, and selected-project instruction files.
- Only the tracked project evidence needed to support candidate rules.

## Required Reads

- Read `references/decision-contract.md` completely.
- Read both specs completely, then run `inspect` with their owner receipt.
- Read the resulting manifest and any active selected-project instruction file.
- Read only repository sources needed to validate proposed rule locators and
  commands.

## Writes

- Private manifest, decision, ownership receipt, and final state under the
  caller-owned workflow directory.
- `<selected-project-root>/AGENTS.md` only through the helper and only for an
  authorized v2 transition.

The selected-project file is committed product truth. Private receipts and
state must never be committed.

## Process

1. Require the owner-issued receipt to bind tracked spec files, complete
   status-aware requirements-to-design coverage, exact full-file digests,
   selected project, Git root, scope, owner, validator, and traceability result.
   `inspect` reruns that owner's fixed validator and requires exact receipt
   equality.
2. Always declare the active profile and discovery-sensitive runtime overrides;
   use `null` and `{}` for the base case. Resolve the current
   `$CODEX_HOME/PROFILE.config.toml` profile format before trusted project
   config and runtime overrides. Treat the resulting selected project, layered
   config, instruction chain, target classification, and recovery check as
   authoritative.
3. Keep a rule only when it is durable, project-specific, actionable,
   public-safe, and supported by a tracked evidence record with an exact
   locator. Requirements and design are inputs, not sufficient justification.
4. Store a `project-agent-instructions.decision.v2` decision. For `needed`,
   provide structured rules; the helper renders all Markdown deterministically.
   For the other dispositions, provide no rules.
5. Use exact-digest `adopt` approval before taking ownership of an unreceipted
   intact v2 file. Use exact-digest `retire` approval before deleting an intact
   managed file that is no longer needed. Never migrate v1 automatically.
6. Run `apply`, then `verify`. Never write or delete `AGENTS.md` directly.
7. If state reports `reload_required: true`, stop the current execution
   boundary. Start a fresh Codex session, rerun inspection and verification,
   and explicitly read the active selected-project instructions before any
   planning, contract lock, auto-steering, or worker dispatch.
8. Return the outcome, decision fingerprint, target digest, evidence paths,
   reload status, and any blocker to the caller.

## Decision Rules

- Evaluate the exact selected project, not automatically the Git root.
- A selected subproject must satisfy the effective root-marker config. An empty
  marker list disables parent traversal and treats the selected directory as
  the project root.
- `not-needed` is correct when no meaningful durable project rule remains.
- Rules use only the six renderer-owned sections and stay within 8 preferred,
  12 hard; each rule is at most 256 UTF-8 bytes.
- Prefer at most 2 KiB of generated body. A larger body requires a compact
  justification and may never exceed 4 KiB or effective Codex capacity.
- Verify commands from current scripts, config, task runners, or CI.
- An unmarked or edited file is human-owned and preserved byte-for-byte.
- A same-directory override or configured fallback is the active human-owned
  instruction source and blocks generation when necessary rules are missing.
- Reject ignored targets and untracked or ignored ancestor/human-owned project
  instruction sources. Stage generated project truth before contract commit.
- Global instructions may reveal a conflict but do not affect portable output
  bytes. Never weaken a higher-level security, privacy, authorization,
  publication, or destructive-operation safeguard.
- Treat closer nested instruction files as directory-scoped refinements, not
  authorization to weaken higher-level safeguards.

## Ownership and Recovery

- The v2 marker binds the input manifest, decision, and rendered body digests.
  A separate private ownership receipt binds the exact target bytes.
- Human edits transfer ownership immediately.
- Any lock or backup artifact blocks inspect, create, refresh, adoption,
  retirement, and no-write state recording with `RECOVERY_REQUIRED`.
- Create is exclusive. Refresh and retirement compare exact bytes under a lock
  and preserve a recoverable backup on an interrupted final boundary.
- Retirement is explicit, guarded, and receipt-recorded; it is never inferred
  from `not-needed` alone.
- A v1 marker returns `LEGACY_GENERATED_FILE`; resolve it manually rather than
  adding a compatibility path.

## Idempotency

- Identical rules with valid ownership and current portable provenance preserve
  bytes, mode, and mtime; spec, renderer, or evidence projection drift refreshes
  the marker even when the body is unchanged.

## Failure Handling

- `SPEC_VALIDATION_REQUIRED`: owner receipt is absent, malformed, or stale.
- `DISCOVERY_CONTEXT_UNVERIFIED`: effective config, profile, overrides, trust,
  or selected-project marker context is ambiguous.
- `RECOVERY_REQUIRED`: a lock or backup requires explicit recovery.
- `ADOPTION_APPROVAL_REQUIRED` or `RETIREMENT_APPROVAL_REQUIRED`: exact target
  authorization is missing.
- `OWNERSHIP_CONFLICT`: private ownership evidence is missing or stale.
- `LEGACY_GENERATED_FILE`: v1 state needs manual resolution.
- `EXISTING_INSTRUCTIONS_GAP` or `INSTRUCTION_CONFLICT`: human-owned active
  instructions require a proposed human resolution.
- `UNSAFE_TARGET`, `CONCURRENT_MODIFICATION`, or `STALE_GENERATED_FILE`: stop
  without bypassing the helper.

Return blockers to the coordinator. Do not create a fallback, loosen a rule,
or remove recovery evidence automatically.

## Must Not

- Do not copy prompts, task state, acceptance-criteria inventories,
  architecture essays, troubleshooting history, generic advice, secrets,
  endpoints, environment values, absolute home paths, or temporary decisions.
- Do not place repeatable procedures in `AGENTS.md`; keep them in skills or
  project docs.
- Do not commit private manifest, decision, ownership, runtime-config, receipt,
  or state files.

## Completion Criteria

- The exact specs and effective discovery context are receipt-bound.
- The result is `created`, `refreshed`, `adopted`, `retired`,
  `existing-sufficient`, `not-needed`, or a structured blocker.
- Any generated file is deterministic, concise, public-safe, evidence-backed,
  provenance-valid, and at the selected project root.
- Verification passes and any required session reload finishes before the
  coordinator continues.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return selected project, active instruction source, outcome, decision and
target digests, evidence paths, `reload_required`, and any blocker. Do not print
raw private rationale, prompts, credentials, or environment values.
