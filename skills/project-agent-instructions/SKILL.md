---
name: project-agent-instructions
description: "Use only when explicitly routed by maintain-project-specs with its current canonical requirements/design receipt; conditionally render, create, refresh, adopt, or retire a concise selected-project AGENTS.md with deterministic rules, explicit ownership, deferred terminal sealing, and fail-closed recovery."
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

Use only when `maintain-project-specs` explicitly routes here with its current
canonical receipt. Task Implementer and Agentic SDLC are outer consumers and
must route through that owner instead of invoking this skill directly. Keep
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Do not expose
a standalone public workflow command.

## When To Use

- `maintain-project-specs` has issued its current canonical receipt and routes
  the selected project here before implementation opens.
- Task Implementer or Agentic SDLC needs a project-instructions decision and
  has routed through `maintain-project-specs` as the sole direct owner.
- Specs, selected-project identity, relevant evidence, effective Codex config,
  ancestor instructions, renderer, target, or prior decision changed.

## When Not To Use

- Do not invoke directly outside `maintain-project-specs`.
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
  required `<lifecycle-session>/runtime-config.json` declaration. Use explicit
  `null` and `{}` values when neither applies.
- Explicit active Codex home passed to `inspect`; lifecycle routing never
  relies on the helper's environment fallback.
- Applicable global, ancestor, and selected-project instruction files.
- Only the tracked project evidence needed to support candidate rules.

## Required Reads

- Read `references/decision-contract.md` completely.
- Read both specs completely, then run `inspect` with their owner receipt.
- Read the resulting manifest and any active selected-project instruction file.
- Read only repository sources needed to validate proposed rule locators and
  commands.

## Writes

- Caller-authored `runtime-config.json` beside the workflow directory and
  `decision.json` directly within it; the lifecycle hook admits only these
  exact non-authoritative inputs for the current session.
- Coordinator-authored manifest, render state, ownership receipt, and final
  state under the caller-owned workflow directory.
- The managed tail region of `<selected-project-root>/AGENTS.md` only through
  the helper and only for an authorized v3 transition. Human-authored prefix
  bytes remain outside skill ownership.

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
   authoritative. Run lifecycle-owned inspection as one uncomposed command
   with explicit `--codex-home` and absolute current-session receipt, runtime,
   private-root, and output paths.
3. Keep a rule only when it is durable, project-specific, actionable,
   public-safe, and supported by a tracked evidence record with an exact
   locator. Requirements and design are inputs, not sufficient justification.
   Treat a canonical statement that existing users depend on stable behavior
   and future code or interface changes must not break them as explicit
   compatibility intent without requiring `GA`, `backward compatibility`, or
   another prescribed phrase.
4. Store the `project-agent-instructions.decision.v3` decision as the exact
   private `decision.json` input. For `needed`,
   provide structured rules; the helper renders all Markdown deterministically.
   For the other dispositions, provide no rules.
5. Use exact-digest `adopt` approval before taking ownership of an unreceipted
   intact v3 region. The same approval may re-adopt an intact region when the
   active receipt still binds the exact project, target, and body but another
   authorized lifecycle refreshed only its portable marker projection. Any
   subject or body mismatch remains an ownership conflict. Use exact-digest
   `retire` approval before removing an intact managed region that is no longer
   needed. Never migrate v1 or v2 markers automatically.
6. Before implementation, run `render` and pass its exact private rules file
   to `maintain-project-specs plan`. Do not mutate the repository yet.
7. After final requirements/design reconciliation, run `apply`, then `verify`
   as the terminal seal mutation. Never write or delete `AGENTS.md` directly.
8. If state reports `reload_required: true`, stop the current execution
   boundary. Start a fresh Codex session, rerun inspection and verification,
   and explicitly read the active selected-project instructions before any
   planning, contract lock, auto-steering, or worker dispatch.
9. Return the outcome, decision fingerprint, target digest, evidence paths,
   reload status, file effect, and any blocker to the caller. For
   `not-needed`, state explicitly that no file was created or changed and that
   a missing target remains absent.

## Decision Rules

- Evaluate the exact selected project, not automatically the Git root.
- For nonempty effective root markers, resolve the nearest matching directory
  from the selected project through the enclosing Git root and scan
  instructions from that discovery root down to the selected project. Do not
  search above the Git root. An empty marker list disables parent traversal and
  treats the selected directory as the discovery root.
- Explicit existing-user no-break intent requires a durable project rule unless
  active same-directory project instructions already express the equivalent
  compatibility contract. Do not let a conflicting personal global default
  suppress that rule.
- The default compatibility scope is supported observable behavior and public
  interfaces: APIs and public import paths, CLI commands, flags, output and exit
  behavior, configuration schemas and defaults, persisted formats, and upgrade
  paths. Breaking one requires explicit approval, a deprecation or migration
  plan, and regression coverage; private internals keep one canonical path.
- Prefer these two `Change requirements` rules for that default contract:
  "This project has existing users. Preserve supported behavior and public
  interfaces across changes; treat unintended compatibility breakage as a
  regression." and "Breaking a supported API, CLI contract, configuration or
  persisted format, or upgrade path requires explicit approval, a deprecation
  or migration plan, and regression coverage. Keep internals on one canonical
  path."
- `not-needed` is correct when no meaningful durable project rule remains.
- A missing `AGENTS.md` is not evidence that one is needed. Missing plus
  `not-needed` is a verified successful no-file outcome, not a creation
  failure.
- Rules use only the six renderer-owned sections and stay within 8 preferred,
  12 hard; each rule is at most 256 UTF-8 bytes.
- Prefer at most 2 KiB of generated body. A larger body requires a compact
  justification and may never exceed 4 KiB or effective Codex capacity.
- Verify commands from current scripts, config, task runners, or CI.
- An unmarked file is human-owned. When rules are needed, attach one managed
  tail region while preserving every existing byte as its human prefix.
- Human edits to the prefix remain human-owned and do not transfer managed
  region ownership. Any marker or managed-body edit fails closed.
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

- The v3 tail marker binds the input manifest, decision, and rendered body
  digests. A separate private ownership receipt binds that exact region and
  project path; it does not claim the human-authored prefix.
- Human prefix edits remain allowed. Edits inside the managed tail transfer
  that region out of automation ownership immediately.
- Any lock or backup artifact blocks inspect, create, refresh, adoption,
  retirement, and no-write state recording with `RECOVERY_REQUIRED`.
- Create is exclusive. Attach, refresh, and retirement compare exact whole-file
  bytes under a lock, preserve the prefix byte-for-byte, and retain a
  recoverable backup on an interrupted final boundary.
- Retirement is explicit, guarded, and receipt-recorded; it is never inferred
  from `not-needed` alone.
- A v1 or v2 marker returns `LEGACY_GENERATED_FILE`; resolve it manually rather
  than adding a compatibility path.

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
- The result is `created`, `attached`, `refreshed`, `adopted`, `retired`,
  `existing-sufficient`, `not-needed`, or a structured blocker.
- The result reports the exact file effect; `not-needed` never implies that a
  missing file should have been created.
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
target digests, evidence paths, exact file effect, `reload_required`, and any
blocker. For `not-needed`, say that no file was created or changed and that a
missing target remains absent. Do not print raw private rationale, prompts,
credentials, or environment values.
