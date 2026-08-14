# Prompt Session Intake

`prompt-session-intake` is the non-blocking capture sidecar between a safe
direct Codex prompt and an explicitly bound Task Implementer or Agentic SDLC
objective. It is not an implementation workflow and has no standalone public
action. The direct prompt always reaches the current agent and remains
authoritative even when capture is skipped or fails.

An exact `$task-implementer workspace init`, `$task-implementer run`,
`$sdlc-start workspace init`, or `$sdlc-start run` invocation binds the current
Codex session to one workflow and canonical project. Later safe direct turns in
that session follow this split:

1. The `UserPromptSubmit` hook always passes the direct prompt through. It
   stages only event-v2 identity, digest, workflow, project, token, and timing
   metadata when capture is safe; it never writes the submitted body or a raw
   prompt journal. Secrets and capture-state failures do not stop the turn.
2. The current agent records `merge`, `noop`, or `sensitive`. Workflow/skill,
   shell/tool, delivery, agent-control, status, conversation, unrelated, and
   duplicate-only turns are no-ops. A later sensitive finding discards
   body-derived capture authority.
3. A merge uses only durable project objectives, contracts, decisions,
   constraints, acceptance outcomes, facts, examples, corrections, and
   operational requirements. Mixed prompts omit ephemeral execution and
   conversation clauses; commands remain only when they define project
   behavior, examples, or verification contracts.
4. The coordinator accepts a private `project-intent.md` projection against
   the exact canonical prompt digest. Losslessness applies to selected project
   intent, not to excluded wrappers.
5. Task Implementer or SDLC rehashes that accepted projection and performs a
   compare-and-set create/append with one marker bound to both operation ID and
   projection digest. Exact operation retries and byte-identical projection
   duplicates do not append again. Capture never starts or resumes a workflow.
6. The event is consumed with the full prompt identity and short prompt
   reference. Writer-session identity remains diagnostic provenance only.

Explicit bound workflow runs register the authoritative active prompt result,
and verified terminal results close that registry entry. This lets a fresh
session attach only when one active objective is unambiguous, without treating
a stale writer, queued prompt, or file save as active work.

For Task Implementer, a bound project may be the primary checkout or its
managed-lane project only after the workspace manifest canonically proves the
same scope under both roots and the lane `source_root`. Unrelated projects
remain fail-closed.

Prompt compare-and-set, projection-bound operation markers, a current-session
and current-event claim, and the workflow scope lock protect canonical writes.
Concurrent same-base merges keep one winner and one drift result; capture never
auto-rebases. The prompt-session Stop delegate never blocks completion or
requests a continuation for incomplete capture. Only an explicit
`$task-implementer run` or `$sdlc-start run` executes a workflow.

Metadata-only event-v2 transition state and accepted project-intent projections live under
`${CODEX_HOME:-$HOME/.codex}/prompt-session-intake/`, outside Git, using private
POSIX modes. Event-v1 records and raw journals remain inert in their old
namespace and have no compatibility reader. Manual prompt edits and file saves never trigger work; users still
run `$task-implementer run <prompt-ref-or-file>` or
`$sdlc-start run <prompt-ref-or-file>` explicitly.

Source presence does not prove activation. Installing the hook files,
registering and reviewing/trusting them, restarting Codex, and verifying a
fresh bound session are separate opt-in steps.

Focused source tests:

```bash
python3 prompt-session-intake/assets/hooks/tests/test_prompt_session_intake.py -v
python3 prompt-session-intake/scripts/test_prompt_session.py -v
python3 prompt-session-intake/scripts/test_skill_contract.py -v
```

See [the state contract](references/state-contract.md) for identity,
transition, concurrency, and capture-isolated failure rules.
