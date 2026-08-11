# Prompt Session Intake

`prompt-session-intake` is the internal bridge between a safe direct Codex
prompt and an explicitly bound Task Implementer or Agentic SDLC workflow. It is
not a third implementation workflow and has no standalone public action.

An exact `$task-implementer workspace init`, `$task-implementer run`,
`$sdlc-start workspace init`, or `$sdlc-start run` invocation binds the current
Codex session to one workflow and canonical project. Later safe direct turns in
that session follow this split:

1. The capture-only `UserPromptSubmit` hook rejects recognized secrets before
   persistence and stages exact private session/turn provenance.
2. The current agent classifies the turn. Conversation, status, and control
   are consumed without prompt changes or execution.
3. Material intent is refined for grammar, semantic clarity, and concision
   without dropping facts, data, constraints, uncertainty, references, or
   acceptance intent.
4. The coordinator accepts the refinement against the exact canonical prompt
   digest and operation ID. Task Implementer or SDLC performs the
   compare-and-set create/append, returns the already-applied prompt on an exact
   interrupted retry, and continues its existing run path exactly once.
5. The event is consumed with the full prompt identity and short prompt
   reference. The shared Stop arbiter releases the session writer.

Explicit bound workflow runs register the authoritative active prompt result,
and verified terminal results close that registry entry. This lets a fresh
session attach only when one active objective is unambiguous, without treating
queued prompts or file saves as active work.

Raw session journals and transition state live under
`${CODEX_HOME:-$HOME/.codex}/prompt-session-intake/`, outside Git, using private
POSIX modes. Manual prompt edits and file saves never trigger work; users still
run `$task-implementer run <prompt-ref-or-file>` or
`$sdlc-start run <prompt-ref-or-file>` explicitly.

Source presence does not prove activation. Installing the hook files,
registering and reviewing/trusting them, restarting Codex, and verifying a
fresh bound session are separate opt-in steps.

Focused source tests:

```bash
python3 prompt-session-intake/assets/hooks/tests/test_prompt_session_intake.py -v
python3 prompt-session-intake/scripts/test_prompt_session.py -v
```

See [the state contract](references/state-contract.md) for identity,
transition, concurrency, and fail-closed rules.
