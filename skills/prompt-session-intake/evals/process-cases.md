# Prompt Session Intake Process Cases

Apply every routed case once with a current Task Implementer receipt and once
with a current Agentic SDLC receipt. The direct request always proceeds in the
current agent. Capture never starts, resumes, or selects either workflow.
`trigger-prompts.csv` is the sole canonical trigger authority; these cases
supplement it with projection, concurrency, and failure-path expectations.
Contract tests remain required for capture, lifecycle, and output assertions;
canonical CSV validation does not replace them. Each process row below is
bound to a canonical routing row ID and evaluates only post-routing behavior.

## Merge: durable project intent

| Routing row | Expected capture |
| --- | --- |
| `prompt-intake-positive-04` | `merge` / `intent`; preserve the default and value. |
| `prompt-intake-positive-05` | `merge` / `constraint`; preserve the upper bound. |
| `prompt-intake-positive-06` | `merge` / `acceptance-change`; preserve the exact criterion. |
| `prompt-intake-positive-07` | `merge` / `clarification-answer`; preserve the choice and negation with enough project context. |
| `prompt-intake-positive-08` | `merge`; retain the command as a declarative interface contract, never execute it. |
| `prompt-intake-positive-09` | `merge`; retain the command as a verification requirement, never execute it. |
| `prompt-intake-positive-10` | `merge`; retain the inert negative example and never execute it. |

For every merge, the mode-0600 `project-intent.md` is concise and lossless for
selected project facts, values, negations, decisions, uncertainty, examples,
references, constraints, and acceptance outcomes. Acceptance, adapter merge,
and consumption use the same projection digest.

## No-op: direct control or conversation

| Routing row | Expected no-op reason |
| --- | --- |
| `prompt-intake-negative-03`, `prompt-intake-negative-04`, `prompt-intake-negative-05` | Binding/routing only; never sidecar-merge the invocation. |
| `prompt-intake-negative-01`, `prompt-intake-negative-06` | `workflow-control` |
| `prompt-intake-negative-07`, `prompt-intake-negative-08` | `tool-control` |
| `prompt-intake-negative-09` | `delivery-control` |
| `prompt-intake-negative-10`, `prompt-intake-negative-11` | `agent-control` |
| `prompt-intake-negative-02`, `prompt-intake-negative-12` | `status` unless the answer itself supplies a durable requested decision. |
| `prompt-intake-negative-13` | `conversation` |
| `prompt-intake-negative-14` | `unrelated` |
| `prompt-intake-negative-21` | `duplicate` terminal no-op with no append. |

No-op accepts and consumes without a projection, canonical prompt mutation,
run manifest, active pointer, coordinator, or workflow transition.

## Mixed turns: project-intent projection only

| Routing row | Project intent retained | Ephemeral clause excluded |
| --- | --- | --- |
| `prompt-intake-positive-11` | Configurable timeout | Immediate test command |
| `prompt-intake-positive-12` | JSON output requirement | Status request |
| `prompt-intake-positive-13` | Exit-code contract | Agent orchestration |
| `prompt-intake-positive-14` | PostgreSQL choice | Immediate test action |
| `prompt-intake-positive-15` | Supported CLI command | Immediate pytest action |
| `prompt-intake-negative-15` | Nothing | Entire turn is current-turn control/status. |

The canonical prompt must contain all and only the retained column. Exclusion
never prevents the current agent from handling the complete direct request.

## Clarification and ambiguity

- Canonical rows `prompt-intake-positive-16` and
  `prompt-intake-positive-17` are `clarification-answer` only when a current
  project question supplies unambiguous context. Preserve the resolved choice
  with that context.
- Canonical row `prompt-intake-negative-16`, or another ambiguous fragment
  without a relevant open question, is conversation or a capture-only blocker.
  Never invent project intent; continue the direct request.

## Sensitive and invalid input

- Canonical row `prompt-intake-negative-17` covers pre-stage recognized-secret
  rejection; it never authorizes partial capture.
- A recognized secret, including secret plus valid project intent, reaches the
  agent but creates no event, projection, or canonical update. Never partially
  redact and capture the remainder.
- A secret found after staging uses `sensitive`; the terminal discard retains
  no submitted digest, operation ID, acceptance token, or projection.
- Oversize/NUL input, unsafe modes or links, malformed state, reserved marker
  input, and capture conflicts skip or fail capture without a block decision,
  body echo, replay, or Stop continuation.

## Identity, retries, and concurrency

- Same session, turn, and submitted digest returns the same event. Reusing the
  turn with different bytes is a capture conflict and leaves the first receipt
  unchanged.
- A later staged turn invalidates first classification of an older staged
  event. An already-accepted immutable event may finish only its exact merge.
- Replacing accepted projection A with B fails before canonical mutation.
- The exact operation-and-projection marker occurs once. Retrying it returns
  the existing result. A byte-identical projection under another operation is
  a terminal duplicate; semantic paraphrase detection remains agent-owned.
- Two distinct same-base merges serialize: one wins and one reports prompt
  drift without overwrite or automatic rebase. Both direct requests proceed.
- Task Implementer primary and manifest-proven lane paths resolve one logical
  objective; unrelated projects do not capture.
- Event-v1 files and raw journals remain byte-unchanged and unread while the
  same logical turn stages only in the version-separated event-v2 namespace.

## Unbound, manual, Stop, and failure cases

- Canonical row `prompt-intake-negative-20` covers unbound use: without an exact
  current hook receipt, do not use this skill.
- Canonical row `prompt-intake-negative-18` covers manual prompt-file edits,
  which remain inert until explicit
  `$task-implementer run <prompt-ref-or-file>` or
  `$sdlc-start run <prompt-ref-or-file>`.
- Canonical row `prompt-intake-negative-19` covers older-event replay. Never
  replay it or stage a Stop-generated, compaction, system, or subagent prompt.
- Unsafe, ambiguous, stale-writer, conflicting, or unavailable capture state
  cannot stop the direct request and cannot request a Stop continuation.
