---
name: config-claude
description: "Use only when explicitly asked to configure, reconcile, inspect, or recover a personal Claude Code setup: native settings, instructions, roles, hooks, private task state, and selected MCP integrations."
disable-model-invocation: true
---

# Config Claude

## Help

For `$config-claude --help` or `$config-claude -h` (including native Claude forms), return concise help and stop before
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

## Agent Compatibility

This skill configures **Claude Code**, regardless of which agent executes it.
Keep its native configuration formats and product-specific commands.

Use `$config-claude` in Codex, `/config-claude` in Claude Code, or
`/skills:config-claude` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Invocation

- `$config-claude [request]`: invoke from Codex.
- `/config-claude [request]`: invoke a direct Claude skill.
- `/skills:config-claude [request]`: invoke this repository's Claude plugin skill.
- `request`: configure, reconcile, inspect, or recover missing settings.
- `-h, --help`: show concise report-only help.

No additional public flags. Helper arguments belong to their documented scripts.

## Purpose

Configure Claude Code using native user files while preserving personal
configuration. This skill can be invoked from either Codex or Claude; its
target is always Claude. Use `config-codex` for Codex configuration and
`global-context-management` for ordinary task execution.

## Invocation Policy

Use only after explicit user invocation.
Set `allow_implicit_invocation: false` for this setup skill. An explicit request
selects configuration scope. An inspection or recovery question alone
does not authorize applying changes. A configuration request authorizes its
scoped edits; retain any more specific script-execution or approval rules from
the session. Installing this skill does not configure the user's machine.

## Workflow

1. Resolve the target from the user's explicit Claude home, then
   `CLAUDE_CONFIG_DIR`, then `~/.claude`. Never use `CODEX_HOME` as a fallback.
   Read [references/local-setup.md](references/local-setup.md) for setup or
   reconciliation; read [references/config-recovery.md](references/config-recovery.md)
   before recovering missing settings.
2. Inspect target files structurally without printing values or secrets. Identify
   the installed source catalog and local or plugin hook route. Preflight all
   dependencies needed for the requested changes before writing. A single-skill
   installation can inspect its native configuration, but full hook setup needs
   the complete repository package; report missing dependencies precisely.
3. Use `scripts/check-local-idempotency.py` for a read-only convergence check.
   Select only requested optional checks. If all requested surfaces already
   match, report no changes and stop without touching files or creating backups.
4. Prepare a minimal patch. Existing `CLAUDE.md` and `settings.json` are
   patch-only: preserve unrelated content, formatting, ordering and values.
   Add missing guidance in one `config-claude` managed block; update only its
   contents when stale. Never treat recovery templates as desired state for
   existing user settings. Conflicting personal values need a concrete decision
   before replacement unless that exact change is already authorized.
5. Apply authorized changes sequentially. Back up only changed files privately.
   Recheck file identity and bytes against the inspected version before each
   edit; use an exact-match patch and stop on unexpected drift. Reread after any
   installer or native CLI writer. Never restore a stale whole-file snapshot.
   Create missing settings through the exclusive recovery helper when execution
   is authorized. Copy missing roles; replace a changed role only with proven
   prior managed provenance or an explicitly approved diff.
6. Install the three native role templates and private task-state directory.
   Before first hook installation, create a missing local delegation policy
   with `auto_read_only_subagents: false` unless delegation was selected. Keep
   existing policy unchanged absent a requested change; reinstallations preserve
   this operator-owned policy.
   Reuse the existing hook installer or plugin registration; do not create a
   new hook bundle. Preserve workflow handlers, one Stop arbiter, native
   identities and Task Implementer/SDLC ownership. A plugin-owned hook route
   must not gain duplicate local registrations.
7. Configure only selected options: trusted-local permissions, delegation
   policy, private Task Implementer storage, and MCP integrations. For MCP,
   read [references/mcp.md](references/mcp.md); use Claude's native user-scope
   commands and secret-variable references. Do not reconstruct Claude's global
   application/authentication JSON or import private Codex configuration.
8. Rerun the checker and inspect the final diff. Report static configuration,
   installed payloads and actual fresh-session activation separately. Follow
   the native validation steps in the setup reference; a template or JSON parse
   does not prove runtime hook execution or model behavior.

## Native Boundaries

- Use `CLAUDE.md`, `settings.json` and Markdown role definitions. Claude does
  not automatically discover `AGENTS.md` or `AGENTS.override.md`. Preserve the
  durable nested-instruction safety principle using Claude's documented file
  hierarchy; do not copy Codex precedence rules or config keys.
- Keep model and provider selection inherited. The optional `trusted-local`
  profile sets `permissions.defaultMode` to `bypassPermissions` and
  `sandbox.enabled` to `false` only after explicit selection. Preserve existing
  stricter settings and managed restrictions absent exact authority to change
  a user-owned value. Report effective restrictions without bypassing them.
- The three role templates expose only `Read`, `Grep` and `Glob`. A parent in
  bypass/auto/acceptEdits mode can override a subagent's `permissionMode`, so
  tool restrictions are required. Verify the effective role before delegation:
  project, CLI and managed definitions can override user roles. Read-only agent
  tools do not imply that inherited hooks cannot update private task state.
- Keep task state separate from Claude auto memory and transcripts. Retain
  `codex-remediation-budget:v1` unchanged as an existing cross-host protocol.
  Automatic delegation and private Task Implementer storage remain opt-in.
- Refuse malformed or duplicate-key JSON, symlinks, special files, unowned
  targets and unexpected concurrent edits. Do not repair user-owned drift by
  replacing an entire configuration. Never disable a guard or choose an
  alternate writer to evade a denial; repair an authorized proven source defect
  at its owner, otherwise report the external restriction.

## Output Contract

Report each requested surface as `Aligned`, `Not aligned`, or `Blocked`, with
the minimal reason, changes and backups, missing dependencies, and next native
verification step. State explicitly when nothing changed. Use `STATIC_PASS`
for deterministic checks, `RUNTIME_PASS` only for observed fresh-host loading,
and `QUALITY_PASS` only for a valid comparative evaluation. Mark unrun or
unavailable lanes; never expose secrets or raw configuration values.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
