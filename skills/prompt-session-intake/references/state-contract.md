# Prompt Session State Contract

The capture hook owns only private session provenance and transition state. It
does not refine user intent, edit a workflow prompt, select tasks, or execute a
workflow.

## Identity and ownership

- A raw Codex session identifier is hashed before it appears in a directory or
  registry key.
- One binding joins that session to one canonical project directory and one of
  `task-implementer` or `agentic-sdlc`.
- Only an exact Task Implementer or Agentic SDLC init/run invocation in that
  Codex session creates the binding. An active objective registry entry never
  binds another session, even when it is the only compatible entry.
- The full workflow prompt ID remains canonical objective identity. A short
  prompt reference is presentation and resolution metadata only.
- One registry entry may be active for one logical project. Writer-session
  identity is bounded provenance that may be refreshed or released; it never
  grants a lease or rejects another direct turn.
- A Task Implementer workspace manifest proves that its primary project and
  managed-lane project are aliases of the same logical objective. The registry
  collapses those aliases to one entry and rejects unrelated projects.
- An explicit bound workflow run registers its authoritative active prompt
  result. A verified terminal result closes that registry entry. Queued prompts
  remain inactive until their owning workflow activates them.

## Turn state machine

```text
direct prompt proceeds + optional safe capture -> staged
  -> merge accepted -> consumed
  -> noop accepted -> consumed
  -> sensitive discarded
```

- `staged` is an event-v2 metadata receipt binding exact session, turn,
  submitted digest, project, workflow, private token, timing, and a
  domain-separated non-secret operation ID. It never persists the submitted
  body or a raw journal. Event-v1 remains inert in its separate old namespace.
- The session's monotonic current-event receipt authorizes first
  classification. A later staged turn makes an older staged event ineligible;
  an already-accepted immutable transition may still finish exactly.
- `merge` acceptance records one material classification, a canonicalized
  mode-0600 `project-intent.md`, its digest, and the canonical prompt base
  digest. Manual drift rejects acceptance before workflow intake.
- `noop` acceptance records one bounded reason and no projection or prompt
  mutation input. Reasons distinguish workflow, tool, delivery, agent, status,
  conversation, unrelated, and duplicate-only turns.
- `sensitive` atomically becomes terminal `discarded` and removes the submitted
  digest, operation ID, acceptance token, and projection authority.
- A consumed merge records the exact resulting full prompt identity,
  reference, duplicate outcome, and canonical path/digest. The canonical
  prompt must contain either the exact operation-and-projection marker once or,
  for an exact duplicate outcome, one prior marker bound to the same byte
  projection, so an interrupted coordinator retry recognizes the
  already-applied merge without creating or appending again. Consumption
  retries must match every recorded result field; the state is then idempotent
  and terminal.

Only `intent`, `steering`, `constraint`, `clarification-answer`, and
`acceptance-change` are merge classifications. Durable objectives, scope,
behavior, interfaces, configuration/schema/data contracts, architecture,
constraints, acceptance outcomes, facts, examples, priorities, non-goals,
trade-offs, corrections, clarification answers, and rollout or operational
requirements are eligible. Mixed prompts exclude ephemeral workflow/skill,
shell/tool, delivery, agent-control, status, response-style, and conversation
clauses. Commands remain eligible only as declarative project contracts,
examples, or verification requirements.

## Capture isolation rules

- Every `UserPromptSubmit` result is non-blocking. Structured state errors and
  unexpected failures may skip capture with bounded context but cannot prevent
  the current agent from handling the direct prompt.
- Scan for recognized secrets before creating the event. A match skips
  persistence while the direct prompt continues.
- Never infer an older pending event. Accept only the exact event path, token,
  current Codex session, and current-event receipt supplied for the turn.
- Stop-generated continuation, compaction, system, and subagent prompts are
  capture-ineligible.
- Because the documented `UserPromptSubmit` payload has no continuation-origin
  field, the shared Stop arbiter records only the exact combined continuation
  reason digest. The next matching prompt consumes that one-shot marker; a
  different real user prompt clears it and follows normal intake.
- Reject symlinks, non-regular files, multiple links, unsafe modes, stale prompt
  digests, mismatched prompt frontmatter, canonical prompt results outside the
  exact bound project's workspace manifest, cross-project or cross-workflow
  consumption, projection content that uses the reserved
  `prompt-session-operation` marker namespace. Prompt and operation CAS remain
  fail-closed even though direct delivery does not.
- For Task Implementer, accept the event's source-checkout project only when it
  is either the workspace's canonical `primary_root` plus `scope` or its
  verified managed-lane `source_root`; independently prove that `repo_root`
  plus the same scope equals that `source_root` before accepting either
  identity.
- Rehash the accepted projection at the workflow adapter. Create a new
  objective with its final projection-bound marker in one exclusive file
  publication. Append retries accept only one exact operation-and-projection
  marker. Byte-identical projection duplicates are terminal no-ops; semantic
  duplicates require agent judgment. Concurrent distinct same-base operations
  retain one winner and one drift result without automatic rebase.
- The shared Stop arbiter applies one 25-second monotonic deadline to delegate,
  report-finalization, and continuation-marker work so it returns before the
  registered 30-second host timeout.
- The prompt-session delegate always passes Stop and releases matching writer
  provenance when safe. Staged, accepted, invalid, or unavailable capture state
  never requests a continuation or blocks another delegate's policy.
- Hook diagnostics contain only bounded state, identifiers, and digests.
  Submitted prompt or project-intent text never appears in hook output.

## Activation boundary

Source tests prove only the repository implementation. Hook installation,
registration, review/trust, Codex restart, and a fresh-session behavior probe
are separate opt-in gates.
