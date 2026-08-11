# Project Contract Lifecycle

## Canonical repository artifacts

- `docs/requirements.md`
- `docs/design.md`
- conditional project-root `AGENTS.md`
- optional `.codex/project-specs.json`

The two spec files contain exactly one managed region each. Human content
outside those regions is preserved byte-for-byte. `AGENTS.md` remains owned by
`project-agent-instructions`; this skill controls when its decision is rendered
and sealed.

## Policy contract

```json
{
  "schema": "maintain-project-specs.project.v1",
  "mode": "managed",
  "scope": "."
}
```

Use `"mode": "disabled"` for the committed project opt-out. No policy file is
equivalent to managed mode.

A valid cwd outside a Git worktree is treated as non-project work and is not
blocked. The managed lifecycle begins after the folder is initialized or
selected as a Git project; canonical validation still requires a committed
baseline.

## State transitions

| Phase | Allowed repository mutations | Exit |
| --- | --- | --- |
| `planning-required` | Canonical requirements/design only | Validate and `plan`, or bounded `waive` |
| `planned` | None directly; only the exact project-instructions coordinator may apply the terminal `AGENTS.md` decision | `open` or verified final `seal` |
| `implementation-open` | Declared selected-project implementation paths | First material write |
| `reconciliation-required` | Implementation paths and canonical specs | Validate and `plan` |
| `seal-armed` | None; only project-instructions verification and lifecycle seal | Verified `seal` |
| `sealed` | None | New user prompt |
| `waived` | Non-contract Markdown only for `documentation-only`; none for other waivers | New user prompt |

Validation requires both canonical specs to be tracked. A newly created spec
may cross that bootstrap boundary only through an exact, uncomposed
`git -C <selected-project> add -N --` command naming one or both canonical
paths. Intent-to-add records no content, remains epoch-neutral, and does not
open general staging or index mutation.

The first implementation write marks reconciliation required. Later edits do
not deadlock; each successful material selected-project write silently advances
the write epoch while scope and protected-path gates remain active until
completion. Routine PostToolUse accounting emits no additional context. Stop is
the sole routine prompt for one accumulated semantic reconciliation. Canonical
requirements/design writes admitted during planning or reconciliation are
epoch-neutral. A late successful material write admitted before a concurrent
plan, armed seal, or seal advances the epoch and returns the lifecycle to
`reconciliation-required`, so stale later evidence cannot complete.
Every `planned` or implementation-capable state includes a verified
project-instructions render receipt and exact pending-rules digest. A verified
empty render is the authoritative `not-needed` result; omitting render evidence
is never equivalent to it. `Not-needed` records verified private state and
leaves a missing project `AGENTS.md` absent; only a `needed` missing-target
decision creates the file.
When canonical specs record that existing users depend on stable supported
behavior, the planned render includes the compatibility rules before
implementation opens. The terminal apply persists the same decision for future
sessions only after reconciliation. A global no-compatibility default is not
copied and cannot suppress the project intent; equivalent active project
instructions may produce `existing-sufficient`.
The exact project-instructions `apply` action advances `planned` to
`seal-armed`, so no new implementation command is admitted after the terminal
`AGENTS.md` decision. A previously admitted write that reports success late
still invalidates that evidence and reopens reconciliation. A new prompt
carries any unfinished implementation or armed seal instead of discarding it.
Lifecycle-managed project-instructions inspect, render, plan, apply, verify,
and seal commands must use the exact current-session
`<session>/project-instructions/` bundle and its canonical filenames. Another
same-project bundle cannot substitute decision evidence after planning, and a
coordinator-shaped command with malformed bindings is denied rather
than classified as an ordinary Python read. The inspect action additionally
requires an explicit active Codex home and absolute current-session receipt,
runtime, private-root, and output paths in one uncomposed command.
For nonempty effective root markers, project-instructions discovery starts at
the nearest matching directory from the selected project through its Git root
and then scans down to the selected project. Empty markers use the selected
directory only. The selected-project lifecycle scope and target never move to
that discovery ancestor.

## Private state

State lives under `${CODEX_HOME}/project-specs/<workspace>/<session>/` with
`0700` directories and `0600` files. Compare-and-swap transitions share an
owner-only lock so stale hook and coordinator writers fail instead of replacing
newer state. The state stores project scope, baseline commit, turn hash, phase,
document and rule digests, a bounded rule-file locator, the verified final
project-instructions state digest and reload result, and a write counter. It
never stores the raw user prompt or repository contents.

The caller may author exactly two non-authoritative inputs through ordinary
write tools: `<session>/runtime-config.json` and
`<session>/project-instructions/decision.json`. Within the lifecycle-owned
`project-specs` root, mixed targets, sibling files, other sessions, symlink
escapes, receipts, manifests, render state, ownership state, final state, and
lifecycle state stay protected. These private writes never advance the project
write epoch. Task state is an external user-file root governed by its own
workflow and passes through this selected-project hook like other fixed
external targets.

The same two caller inputs may be tightened through one exact, uncomposed
numeric mode-`600` or mode-`0600` command naming one current-session regular
file. Other modes, multiple targets, directory mode changes, symlinks, other
sessions, hard-link aliases, and coordinator-owned files remain protected.
The owner-only `validate --output` path is separately bound to the hook session
and may atomically replace only that session's canonical `spec-receipt.json`.

## Hook boundary

`SessionStart` performs a bounded audit across prior session states for the
selected project. `UserPromptSubmit` binds the turn.
`PreToolUse` gates supported write tools, injects exact pending rules, and
provides an opaque current-turn token for owner transitions.
`PostToolUse` marks a material write as reconciliation-required and advances the
write epoch unless the tool response explicitly proves failure. Unknown
responses remain conservative. Successful routine accounting returns no hook
context and the registration carries no static status message. Concurrent
recorders retry compare-and-swap conflicts from freshly loaded state. If a
successful write reports after planning or sealing advanced concurrently, the
recorder invalidates that evidence by reopening reconciliation. Canonical spec
reconciliation in its admitted phases is not implementation accounting.
Recording errors, invalid completed coordinator-shaped calls, and the verified
terminal project-instructions apply remain visible.
The hook is a project-mutation lifecycle guard, not a general executable trust
policy. Ordinary reads, read-only pipelines, common Git inspection, and
user-owned package-manager tools remain available. This includes exact
`git branch --show-current` inspection and `find -exec stat` compositions;
other `git branch` actions and arbitrary `find -exec` helpers remain material.
Explicit writers,
redirections, detached execution, command substitution, unsafe ripgrep helper
options, mutating Git actions, and mutating or unknown MCP methods remain
material. Shell parsing recognizes quoting before control syntax so search data
containing writer names or shell operators does not become a false mutation.

Materiality and lifecycle relevance are separate. A command with fixed,
provable targets wholly outside the selected project passes through this hook
and remains epoch-neutral. Selected-project effects follow the current phase;
mixed internal/external effects and dynamic or unresolved targets fail closed.
Command-specific target extraction accounts for multi-destination directory
creation, explicit target-directory options, hard-link sources, and tree moves.
Only the lifecycle-owned `${CODEX_HOME}/project-specs` control plane remains
protected as an authoritative external-location target. Hooks, configuration,
task state, installed skills, credentials, and other user files are outside
this selected-project hook's ownership and pass through when their effects are
fixed and external. Normal sandbox, permission, domain-hook, credential, and
destructive-action policies continue to own non-lifecycle security.
The single Stop arbiter invokes troubleshooting, project-contract, and SDLC
delegates in deterministic order. Terminal results take precedence; otherwise
it combines every initial blocker into one continuation request. A ready
troubleshooting report is marked delivered only after no peer delegate needs
continuation; otherwise its private obligation stays active for the later
terminal Stop. The project continuation requests an explicit
project-instructions decision and says that `not-needed` is a successful
no-file outcome rather than implying unconditional `AGENTS.md` creation.

When the project-contract delegate reaches `reconciliation-required`, Stop
directs one review of the accumulated selected-project status, staged and
unstaged diff, untracked implementation files, task context, and accepted
intent. Requirements change only for accepted intent or observable contract
changes; design changes only for implemented boundaries, interfaces,
workflows, operations, or meaningful evidence. Implementation-only and reverted
changes may preserve both spec files byte-for-byte, but validation and planning
must still bind the latest write epoch. Ambiguous impact remains unsealed.

An installed lifecycle hook recognizes non-symlinked coordinator helpers only
under the canonical `${HOME}/.agents/skills` user-skill tree. A source-bundle
hook recognizes its exact sibling coordinators for source tests; a copied hook
does not derive repository trust from `${CODEX_HOME}/hooks` or require a
duplicate `${CODEX_HOME}/skills` installation.

Specialized tools and background processes can bypass hook observations.
Reject detached writers, use exact receipts and compare-and-swap state, and
audit again on the next session. Hooks remain guardrails, not an operating
system filesystem boundary.
