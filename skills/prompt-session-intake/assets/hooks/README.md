# Prompt Session Intake Hook Bundle

This source bundle contributes one `UserPromptSubmit` capture hook and the
repository's byte-identical shared Stop arbiter registration.

- `prompt_session_intake.py` reads one hook payload and delegates state handling.
- `prompt_session_storage.py` owns private atomic I/O, locking, identity
  constants, and the validated cross-workflow registry.
- `prompt_session_event.py` validates staged, accepted, and consumed event
  records plus their private raw/refined journals.
- `prompt_session_state.py` is the thin binding, transition, registry, and Stop
  orchestration facade.
- `prompt_session_result.py` validates canonical Task Implementer and Agentic
  SDLC prompt results against the exact event project and workspace manifest.
- `stop_prompt_session_intake.py` evaluates only the exact current event and
  writer release.
- `stop_lifecycle_arbiter.py` composes every available lifecycle delegate; it is
  registered once after installer deduplication and shares one 25-second
  monotonic budget across all delegate and finalization calls.

The submit hook never refines text, edits a canonical workflow prompt, acquires
a Task Implementer or SDLC lock, or runs a workflow. Recognized secrets are
rejected before raw or event files exist. Hook output is bounded and never
contains prompt bodies.
When Stop requests a continuation, the shared arbiter asks this owner to record
only the exact reason digest. That one-shot marker prevents the generated
continuation prompt from being mistaken for new user intent without relying on
an undocumented hook field.

Do not copy these files into `${CODEX_HOME}/hooks`, register them, or claim
runtime activation as part of source-only maintenance. Installation,
registration review/trust, Codex restart, and fresh-session behavior validation
are separate authorized actions.
