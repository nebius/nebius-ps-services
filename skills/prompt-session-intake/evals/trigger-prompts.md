# Prompt Session Intake Trigger Evals

Apply every routed case once with a current Task Implementer receipt and once
with a current Agentic SDLC receipt. The direct request always proceeds in the
current agent. Capture never starts, resumes, or selects either workflow.

## Merge: durable project intent

| Prompt | Expected capture |
| --- | --- |
| `Add configurable request timeout with default 30s.` | `merge` / `intent`; preserve the default and value. |
| `The timeout must not exceed 60s.` | `merge` / `constraint`; preserve the upper bound. |
| `Acceptance requires timeout=30 to pass.` | `merge` / `acceptance-change`; preserve the exact criterion. |
| `Use PostgreSQL, not SQLite.` after the active prompt asks which database to use | `merge` / `clarification-answer`; preserve the choice and negation with enough project context. |
| `The CLI must support \`tool sync --dry-run\`.` | `merge`; retain the command as a declarative interface contract, never execute it. |
| `Document \`pytest -q tests/api\` as the validation command.` | `merge`; retain the command as a verification requirement, never execute it. |
| `Reject \`rm -rf /\` in the command parser.` | `merge`; retain the inert negative example and never execute it. |

For every merge, the mode-0600 `project-intent.md` is concise and lossless for
selected project facts, values, negations, decisions, uncertainty, examples,
references, constraints, and acceptance outcomes. Acceptance, adapter merge,
and consumption use the same projection digest.

## No-op: direct control or conversation

| Prompt | Expected no-op reason |
| --- | --- |
| `$task-implementer run abcde` or `$sdlc-start run abcde` | Binding/routing only; never sidecar-merge the invocation. |
| `Resume the workflow.` or `Run the next wave.` | `workflow-control` |
| `Run pytest -q.` or `git status` | `tool-control` |
| `Deploy this now.` | `delivery-control` |
| `Spawn two agents to review this.` or `Answer in one sentence.` | `agent-control` |
| `What is the current status?` or `Why did you choose that?` | `status` unless the answer itself supplies a durable requested decision. |
| `Thanks, that explanation makes sense.` | `conversation` |
| An unrelated personal request | `unrelated` |
| An exact byte projection already present under another operation | `duplicate` terminal no-op with no append. |

No-op accepts and consumes without a projection, canonical prompt mutation,
run manifest, active pointer, coordinator, or workflow transition.

## Mixed turns: project-intent projection only

| Prompt | Project intent retained | Ephemeral clause excluded |
| --- | --- | --- |
| `Add configurable timeout, then run pytest -q.` | Configurable timeout | Immediate test command |
| `Status please; also require JSON output.` | JSON output requirement | Status request |
| `Use two agents to implement it, and preserve CLI exit code 2.` | Exit-code contract | Agent orchestration |
| `Use Postgres; now run tests.` | PostgreSQL choice | Immediate test action |
| `Run pytest now, and make \`tool check --json\` a supported command.` | Supported CLI command | Immediate pytest action |
| `Do not edit anything; just tell me status.` | Nothing | Entire turn is current-turn control/status. |

The canonical prompt must contain all and only the retained column. Exclusion
never prevents the current agent from handling the complete direct request.

## Clarification and ambiguity

- `Option B.` or `Yes, preserve exit 2.` is `clarification-answer` only when a
  current project question supplies unambiguous context. Preserve the resolved
  choice with that context.
- Bare `yes`, `B`, or another ambiguous fragment without a relevant open
  question is conversation or a capture-only blocker. Never invent project
  intent; continue the direct request.

## Sensitive and invalid input

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

- Without an exact current hook receipt, do not use this skill.
- A manual prompt-file edit remains inert until explicit
  `$task-implementer run <prompt-ref-or-file>` or
  `$sdlc-start run <prompt-ref-or-file>`.
- Never replay an older event or stage a Stop-generated, compaction, system, or
  subagent prompt.
- Unsafe, ambiguous, stale-writer, conflicting, or unavailable capture state
  cannot stop the direct request and cannot request a Stop continuation.
