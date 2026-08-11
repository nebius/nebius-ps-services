# Maintain Project Specs

This skill owns the canonical `docs/requirements.md` and `docs/design.md`
contract used by ordinary project work, Task Implementer, and Agentic SDLC. It
also coordinates the timing of conditional project `AGENTS.md` decisions.

The lifecycle hooks are guardrails: they bind the selected project, require a
current spec receipt before implementation, mark material changes for
reconciliation, inject exact pending project rules, and route unfinished work
through one Stop arbiter. A planned state always carries verified render
evidence, including an authoritative empty `not-needed` result. Semantic
requirements, design, and project-rule decisions remain skill-owned.
PostToolUse accounting is silent for routine successful project writes; it
keeps the write epoch current, including across concurrent recorder races,
and invalidates a plan or seal if an earlier admitted write reports completion
late. Canonical spec reconciliation remains epoch-neutral. Stop requests one
accumulated semantic review, and ordinary
implementation-only or reverted changes may keep both specs byte-identical
after validation at the latest epoch.

The terminal project-instructions step decides whether repository instructions
are actually needed. A verified `not-needed` outcome is successful and leaves
a missing project `AGENTS.md` absent; only a `needed` outcome can create one.
Hook and completion wording reports that distinction explicitly.
Plain-language intent that existing users rely on stable behavior is explicit
compatibility intent without requiring `GA` or another keyword. The owner
captures the supported public API, CLI, configuration, persisted-format, and
upgrade-path contract in the specs, injects matching rules before the current
implementation, and persists them at terminal seal unless active project
instructions are already sufficient. Personal global defaults are never copied
or used to suppress that project rule.
Lifecycle-owned project-instructions commands are bound to the canonical
current-session private bundle; alternate same-project evidence and malformed
coordinator-shaped commands fail closed. Inspection uses one uncomposed
canonical command with the active Codex home declared explicitly and every
receipt, runtime, private-root, and output path absolute within that bundle.
Nested selected projects use the nearest effective root-marker ancestor within
the enclosing Git worktree for instruction discovery while keeping the exact
selected project as the lifecycle and `AGENTS.md` target.

Installed hooks resolve their coordinators from the same canonical
`~/.agents/skills` root used by Codex and `install-skills.sh`. They admit
ordinary investigation, quoted search patterns, read-only `find -exec stat`,
exact `git branch --show-current`, proven effects wholly outside the selected
project, and exact current-session private inputs without turning the lifecycle
into a general command allowlist. Exact selected-project
intent-to-add and mode-`0600` private-input normalization break first-use
bootstrap cycles without allowing general staging or permission changes.
Mixed, dynamic, ambiguous, or authoritative control-plane writes stay denied;
the authoritative control plane is only lifecycle-owned `project-specs`
state, whose receipts and state remain coordinator-owned. Fixed external
config, hooks, task state, installed skills, credentials, and other user files
pass through this selected-project hook to their actual permission and policy
owners. Receipt persistence is limited to the hook-bound session's canonical
`spec-receipt.json`.

Missing files start from the canonical draft templates under
`assets/templates/`; the agent replaces every placeholder from focused README,
documentation, code, and test evidence before the shared validator may issue a
current receipt. A policy file can disable automation only when it is safe and
exactly matches its committed Git blob.

See [SKILL.md](SKILL.md) for the workflow,
[references/lifecycle.md](references/lifecycle.md) for state and hook behavior,
and [references/migration.md](references/migration.md) for legacy conversion.

Source validation, hook installation, installed parity, and live activation
are separate gates. Installing or registering the hook bundle requires an
explicit mutating installation action, a Codex restart, and `/hooks` review.
