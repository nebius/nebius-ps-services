# Trigger prompts

## Should trigger only through a coordinator

- `maintain-project-specs` has issued its current canonical receipt; route to
  `$project-agent-instructions` before implementation opens.
- Task Implementer or Agentic SDLC needs a project-instructions decision;
  route through `maintain-project-specs` as the sole direct owner.

## Decision outcome cases

- A selected project has no `AGENTS.md`, and tracked evidence supports no
  durable project-specific rules. Return `not-needed`, verify the private
  outcome, and state that the missing file remains absent.
- A selected project has no `AGENTS.md`, and tracked evidence supports one
  durable project-specific verification rule. Return `needed`; the terminal
  apply may create the file and requires a fresh session.

## Should not trigger

- Create a README for this project.
- Invoke `$project-agent-instructions` directly without
  `maintain-project-specs`.
- Add a temporary rule for only this task.
- Update my global `~/.codex/AGENTS.md`.
- Create an `AGENTS.override.md` in every subdirectory.
