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
The same owner exposes an internal pure prompt-impact validator for Task
Implementer and Agentic SDLC. It reparses the canonical specs, requires one
bounded disposition for every extracted statement occurrence, verifies active
requirement/design mappings, and derives retain/replan without storing prompt
text or trusting a workflow-authored aggregate. Workflow adapters own only
private append-only persistence and progression gates.
PostToolUse accounting is silent for routine successful project writes; it
keeps the write epoch current, including across concurrent recorder races,
and invalidates a plan or seal if an earlier admitted write reports completion
late. Canonical spec reconciliation remains epoch-neutral. Stop requests one
accumulated semantic review. An initial Stop from `implementation-open`
atomically enters `reconciliation-required` and clears stale planning bindings
before generating that continuation, so reconciliation never depends on a
synthetic turn firing `UserPromptSubmit`. Ordinary
implementation-only or reverted changes may keep both specs byte-identical
after validation at the latest epoch.
An apply command that cannot produce independently verified final instruction
state also invalidates its plan and reopens reconciliation, so a blocked or
partially applied decision can be corrected without a new user prompt.

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
current-session private bundle. The sole alternate bundle admits exact
Task Implementer inspect and render commands for the active prepared run during
implementation-open or reconciliation-required after the canonical adapter
attests the integration checkout and every run-owned path; apply and verify remain on the current-session terminal seal path. Every
other alternate same-project bundle and malformed coordinator-shaped command
fails closed. Delegated worker commits retain their command-derived worker
session after the adapter matches it to the running task plane and canonical
evidence, while direct commits remain bound to the outer hook payload session.
An attested delegated command may use either the hook runtime or an exact
PATH-canonical executable named `python3` or `python3.N`. A different canonical
Python version is therefore valid, while arbitrary same-name paths, wrappers,
alternate helpers, and mismatched sessions remain denied.
The adapter also owns one narrow integration-review recovery: an exact
completed-follow-up run whose private receipt matches the rejected candidate,
findings digest, and unchanged lane/source heads may reopen the current
zero-write `non-project` promotion waiver as `reconciliation-required`.
Ordinary, written, or unrelated waivers remain terminal.
Inspection uses one uncomposed canonical command with the active
Codex home declared explicitly and every receipt, runtime, private-root, and
output path absolute within the owning bundle.
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
`spec-receipt.json`. Exact task-owned temporary-tree cleanup is classified as
external only for one literal absolute system-temp descendant in the form
`find /tmp/<task-owned-tree> -depth -delete`; temporary roots, variables,
globs, multiple roots, symlinks, and alternate `find` actions remain denied by
this lifecycle, while destructive-action policy still owns deletion approval.

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
