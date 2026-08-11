# Prompt Session State Contract

The capture hook owns only private session provenance and transition state. It
does not refine user intent, edit a workflow prompt, select tasks, or execute a
workflow.

## Identity and ownership

- A raw Codex session identifier is hashed before it appears in a directory or
  registry key.
- One binding joins that session to one canonical project directory and one of
  `task-implementer` or `agentic-sdlc`.
- The full workflow prompt ID remains canonical objective identity. A short
  prompt reference is presentation and resolution metadata only.
- One registry entry may be active for an exact project. Its writer lease is
  held while a turn is executing and released only at a successful Stop
  boundary.
- An explicit bound workflow run registers its authoritative active prompt
  result. A verified terminal result closes that registry entry. Queued prompts
  remain inactive until their owning workflow activates them.

## Turn state machine

```text
safe direct prompt -> staged -> accepted -> consumed
```

- `staged` binds exact session, turn, prompt digest, project, workflow, and a
  private acceptance token plus a deterministic non-secret operation ID. The
  raw prompt is a mode-0600 private journal file.
- `accepted` records one classification. A material class also records the
  lossless refined input and the canonical prompt base digest. Manual drift
  rejects acceptance before workflow intake.
- `consumed` records the exact resulting full prompt identity, reference,
  canonical path/digest, and optional run identity after the workflow starts or
  resumes. The canonical prompt must contain the exact operation marker once,
  so an interrupted coordinator retry recognizes the already-applied merge
  without creating or appending again. Consumption retries must match every
  recorded result field; the state is then idempotent and terminal.

Only `intent`, `steering`, `constraint`, `clarification-answer`, and
`acceptance-change` are material. `conversation`, `status`, and `control` are
accepted and consumed without a prompt mutation or workflow execution.

## Fail-closed rules

- Scan for recognized secrets before creating the event or raw journal.
- Never infer an older pending event. Accept only the exact event path, token,
  session/turn digests, and prompt digest supplied for the current turn.
- Stop-generated continuation, compaction, system, and subagent prompts are
  capture-ineligible.
- Because the documented `UserPromptSubmit` payload has no continuation-origin
  field, the shared Stop arbiter records only the exact combined continuation
  reason digest. The next matching prompt consumes that one-shot marker; a
  different real user prompt clears it and follows normal intake.
- Reject symlinks, non-regular files, multiple links, unsafe modes, stale prompt
  digests, mismatched prompt frontmatter, canonical prompt results outside the
  exact bound project's workspace manifest, cross-project or cross-workflow
  consumption, refined content that uses the reserved
  `prompt-session-operation` marker namespace, and another active writer.
- For Task Implementer, bind the event's source-checkout project to the
  workspace's canonical `primary_root` plus `scope`, while separately proving
  that `repo_root` plus the same scope equals its managed-lane `source_root`.
- Create a new objective with its final marker-bearing Ask in one exclusive
  file publication. Append retries accept only one exact operation marker.
- The shared Stop arbiter applies one 25-second monotonic deadline to delegate,
  report-finalization, and continuation-marker work so it returns before the
  registered 30-second host timeout.
- Hook diagnostics contain only bounded state, identifiers, and digests. Raw or
  refined prompt text never appears in hook output.

## Activation boundary

Source tests prove only the repository implementation. Hook installation,
registration, review/trust, Codex restart, and a fresh-session behavior probe
are separate opt-in gates.
