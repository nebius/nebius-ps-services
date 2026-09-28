---
name: project-agent-instructions
description: "Explicitly repair restrictive project instructions, or render, create, refresh, adopt, or retire selected-project AGENTS.md rules with receipt-bound ownership and recovery."
disable-model-invocation: true
---

# Project Agent Instructions

## Help

For `$project-agent-instructions --help` or `$project-agent-instructions -h` (including native Claude forms),
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

## Agent Compatibility

For managed generation, read `references/claude-discovery.md` before Claude
discovery, inspection, application or replay. That helper path keeps canonical
`AGENTS.md` and requires an existing tracked `CLAUDE.md` import. It binds
`--agent` and `--agent-home` on both hosts. Explicit human-rule repair instead
reads and patches the active native instruction source without creating imports
or requiring helper discovery. Respect native instruction precedence.

Use `$project-agent-instructions` in Codex, `/project-agent-instructions` in Claude Code, or
`/skills:project-agent-instructions` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

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

Use only when the user explicitly requests project-instruction mutation or
`maintain-project-specs` routes an explicit repair request here. Managed
generation additionally requires its current canonical receipt. Task Implementer and Agentic SDLC only read already-effective
instructions and may report advisory status; they never invoke, wait for, or
seal this workflow. Keep
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Do not expose
a standalone public workflow command.

## When To Use

- The user explicitly asks to fix existing restrictive project instructions;
  use the focused human-rule repair path below when the affected prose is
  human-owned. A spec receipt is required for managed generation, not merely
  for editing human-owned prose.
- `maintain-project-specs` has issued its current canonical receipt and routes
  an explicit project-instruction decision here.
- The user explicitly asks to create, refresh, adopt, or retire project
  instructions and the canonical spec receipt is available.
- Specs, selected-project identity, relevant evidence, effective native instruction/configuration context,
  ancestor instructions, renderer, target, or prior decision changed.

## When Not To Use

- Do not infer mutation authority from ordinary project work, Task Implementer,
  Agentic SDLC, or hook observations.
- Do not run from marker checks or unvalidated specification files.
- Do not create generic guidance, task state, architecture prose, reusable
  procedures, or recursive instruction files.
- Do not create `AGENTS.override.md`.

## Inputs

Receipt and runtime inputs below apply to managed generation. Explicit human-rule
repair uses the selected scope, active instructions and exact patch preimage.

- Exact selected project root, enclosing Git root, and spec owner.
- Current `docs/requirements.md` and `docs/design.md`.
- Owner-issued mode-`0600` spec-validation receipt in a caller-owned private
  mode-`0700` directory outside Git.
- On Codex: active profile and discovery-relevant CLI overrides, encoded in the
  required `<lifecycle-session>/runtime-config.json` declaration. Use explicit
  `null` and `{}` values when neither applies.
- Explicit selected native home and agent passed to `inspect`; lifecycle routing never
  relies on the helper's environment fallback.
- Applicable global, ancestor, and selected-project instruction files.
- Only the tracked project evidence needed to support candidate rules.

## Required Reads

- Read `references/decision-contract.md` completely.
- For managed generation, read both specs completely, then run `inspect` with
  their owner receipt.
- Read any active selected-project instruction file and, for managed generation,
  the resulting manifest.
- Read only repository sources needed to validate proposed rule locators and
  commands.

## Writes

- Caller-authored `runtime-config.json` beside the workflow directory and
  `decision.json` directly within it; these are non-authoritative inputs for
  the explicit project-instruction workflow.
- Coordinator-authored manifest, render state, ownership receipt, and final
  state under the caller-owned workflow directory.
- The managed tail region of `<selected-project-root>/AGENTS.md` only through
  the helper and only for an authorized v3 transition. Human-authored prefix
  bytes remain outside skill ownership.
- Human-owned instruction clauses selected by an explicit repair request,
  through a focused native file patch under the repair path below. This does
  not transfer those bytes into generated-region ownership.

The selected-project file is committed product truth. Private receipts and
state must never be committed.

## Explicit Repair Of Existing Instructions

This path applies when the current user asks to repair restrictive or outdated
instructions, including across identified projects in the requested workspace.
The request itself authorizes the specified rule change; do not ask the user
to approve it again. Ordinary implementation and spec maintenance alone do not
authorize instruction changes.

1. Resolve each affected project and read its active instruction chain. Locate
   the actual conflicting clause, its source owner and any managed markers.
   Repair the source template too when it would recreate the obsolete rule.
2. Reconcile the requested policy with system/developer instructions and other
   authority the user cannot change. A user-owned rule can be revised by the
   user's explicit request; it is not an immutable veto on its own repair.
   Do not silently weaken unrelated safeguards or assume unresolved scope.
3. For unmarked human-owned prose or a human prefix outside an intact managed
   region, inspect the exact current bytes, prepare the smallest targeted
   patch, recheck the preimage, and preserve every unrelated byte. An existing
   active override or fallback is the repair target; never create an alternate
   dormant file. No canonical spec receipt is needed solely for this patch.
4. For generated rules, use the existing receipt-bound process below. Never
   hand-edit a managed region, forge ownership, or remove recovery artifacts.
   Resolve missing ownership through exact-digest adoption only when the
   current authorization covers it. A rule-repair request alone does not prove
   ownership or authorize unrelated retirement.
5. Review the diff and reread the active file after repair. Record the exact
   file effect and any remaining conflict. Report `human-rules-repaired`
   separately from helper outcomes; do not fabricate decision or receipt
   digests. Recommend a fresh session for future instruction discovery, while
   continuing currently authorized work under the direct user instruction.

Credential policy must distinguish task authority from secret disclosure:
creating new credentials or secrets necessary for an authorized task's target
and intended access scope does not itself need another confirmation. Store
values only in the intended secret store or protected runtime file. Preserve
approval for uncovered credential replacement/revocation, IAM access expansion,
destructive actions or material impact, and never expose secret values in
artifacts. Do not copy this generic policy into every generated project file;
repair an existing conflicting clause or keep the default in its global owner.

## Process

The following process owns managed generation and refresh. Explicit human-rule
repair above uses native focused editing and does not enter helper transitions.

1. Require the owner-issued receipt to bind tracked spec files, complete
   status-aware requirements-to-design coverage, exact full-file digests,
   selected project, Git root, scope, owner, validator, and traceability result.
   `inspect` reruns that owner's fixed validator and requires exact receipt
   equality.
2. On Codex, declare the active profile and discovery-sensitive runtime overrides;
   use `null` and `{}` for the base case. Resolve the current
   `$CODEX_HOME/PROFILE.config.toml` profile format before trusted project
   config and runtime overrides. On Claude, follow `references/claude-discovery.md`
   for native source declarations and imports. Treat the resulting selected project, layered
   config, instruction chain, target classification, and recovery check as
   authoritative. Run lifecycle-owned inspection as one uncomposed command
   with explicit `--agent <codex|claude> --agent-home` and absolute current-session receipt, runtime,
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
5. During `inspect`, let the helper carry exact active authority from the
   locked workspace-private ownership registry. When that subject has no
   entry, the helper may bootstrap it once only from unanimous exact active
   sealed history; any retirement, mismatch, unsafe evidence, or ambiguity
   publishes a durable blocked subject state. Later evidence removal does not
   rescan that subject; only exact-digest adoption supersedes the block. One
   exact completed apply awaiting registry publication becomes a pending
   generation that no observer may import and only its receipt/state writer may
   recover. A current-session receipt may continue marker-only
   provenance drift when its subject and body still match, but imported
   continuity must match the current whole-target digest. Otherwise use
   exact-digest `adopt` approval before taking ownership of an unreceipted
   intact v3 region. Use exact-digest `retire` approval before removing an
   intact managed region that is no longer needed. Never infer ownership or
   event order from instruction discovery, marker presence, session IDs,
   timestamps, or filesystem metadata, and never migrate v1 or v2 markers
   automatically.
6. Run `render` to produce the exact private rules file without mutating the
   repository. Render
   rejects a disposition that conflicts with the inspected target before it
   publishes evidence. If the current-session decision is revised, changed
   rules replace the prior private rules only under the private-bundle render
   lock and after the final compare-and-swap revalidates the exact predecessor
   state and rules bytes. Rerun the exact same render only when it returns
   `RENDER_STATE_PUBLICATION_INCOMPLETE`; that result means rules are current
   but their matching state I/O publication did not complete. The equal-bytes
   retry re-syncs the private parent directory if replacement completed before
   its directory sync failed. Never delete or overwrite that evidence directly.
7. Only after explicit mutation authorization and final requirements/design
   reconciliation, run `apply`, then `verify` as this workflow's terminal
   mutation. Never write or delete a managed region directly.
8. If state reports `reload_required: true`, stop only this instruction-
   mutation workflow and recommend a fresh session before relying on the new
   rules. Task Implementer and Agentic SDLC remain independent and may continue
   under the instruction chain already loaded for their session.
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
  justification and may never exceed 4 KiB; Codex additionally respects its effective capacity.
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
  bytes. Preserve higher-priority security, privacy, authorization,
  publication, and destructive-operation safeguards. Explicit user changes to
  user-owned policy follow the repair path above without repeated approval.
- Treat closer nested instruction files as directory-scoped refinements, not
  authorization to weaken higher-level safeguards.

## Ownership and Recovery

- The v3 tail marker binds the input manifest, decision, and rendered body
  digests. A separate private ownership receipt binds that exact region and
  project path; it does not claim the human-authored prefix.
- Ordinary sessions continue exact managed-region ownership from one
  serialized, monotonic workspace-private registry entry. A missing entry may
  bootstrap once from unanimous digest-matched, owner-only sealed history;
  unsafe, unsealed, stale, retired, mismatched, damaged, or conflicting
  evidence confers no ownership and publishes a blocked subject state that is
  not retried after evidence removal. The registry root appears atomically with
  a valid generation-zero registry. An interrupted completed apply is bound as
  pending to its exact receipt and state digest; only that writer may promote
  it. Exact adoption may supersede a block; registry retirement is a durable
  tombstone.
- The same registry validator is the sole historical-retention authority.
  Pending, absent-registry legacy, missing final manifest/decision, malformed,
  or mismatched evidence stays protected; matching active, retired, or blocked
  generation plus canonical registry digest snapshots may release the
  historical bundle. Inspect, render, apply, and
  verify hold the stable
  workspace/session maintenance locks before render or ownership locks.
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
  instructions need resolution. Apply an already-authorized human-rule repair,
  then inspect again; otherwise propose the exact unresolved change.
- `RENDER_STATE_PUBLICATION_INCOMPLETE`: rerun the exact same render once to
  finish matching state publication and durability; do not alter its private
  evidence.
- `UNSAFE_TARGET`, `CONCURRENT_MODIFICATION`, or `STALE_GENERATED_FILE`: stop
  without bypassing the helper.

Return unresolved blockers to the coordinator. Do not create a fallback,
change rules without user authority, or remove recovery evidence automatically.

## Must Not

- Do not copy prompts, task state, acceptance-criteria inventories,
  architecture essays, troubleshooting history, generic advice, secrets,
  endpoints, environment values, absolute home paths, or temporary decisions.
- Do not place repeatable procedures in `AGENTS.md`; keep them in skills or
  project docs.
- Do not commit private manifest, decision, ownership, runtime-config, receipt,
  or state files.

## Completion Criteria

- For managed generation, the exact specs and discovery context are receipt-bound.
- For explicit human-rule repair, the active source, original clause, focused
  diff and reread are verified; managed-generation receipts are not invented.
- The result is `created`, `attached`, `refreshed`, `adopted`, `retired`,
  `existing-sufficient`, `not-needed`, `human-rules-repaired`, or a structured blocker.
- The result reports the exact file effect; `not-needed` never implies that a
  missing file should have been created.
- Any generated file is deterministic, concise, public-safe, evidence-backed,
  provenance-valid, and at the selected project root.
- Verification passes before this mutation workflow reports success. Any
  reload recommendation is advisory to independent workflows.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return selected project, active instruction source, outcome, target digest,
exact file effect, `reload_required`, and any blocker. For managed generation,
include decision digest and evidence paths; for human-rule repair mark those
helper fields not applicable. For `not-needed`, say that no file was created or changed and that a
missing target remains absent. Do not print raw private rationale, prompts,
credentials, or environment values.
