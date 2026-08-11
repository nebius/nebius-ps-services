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
- The user says, "People already use this project. From now on, changes to the
  code and interfaces must remain safe for them." Treat this as explicit
  compatibility intent without requiring `GA` or `backward compatibility`.
  Return `needed` with the default compatibility rules unless active
  same-directory project instructions already express the equivalent contract.
- Global instructions say not to add compatibility layers by default, while
  the selected project's canonical specs record explicit existing-user
  compatibility intent. Keep the project rules; do not copy or use the global
  default to suppress them.
- Active human-owned selected-project instructions already protect the same
  supported APIs, CLI/config/persisted formats, and upgrade paths. Return
  `existing-sufficient` with tracked evidence and no generated rules.
- A same-directory `AGENTS.override.md` is active but omits a required
  compatibility rule. Return the fail-closed instructions-gap blocker; do not
  create a dormant `AGENTS.md` behind the override.
- A selected nested project has no local `.git`, but its enclosing repository
  root has the effective marker. Use that ancestor as the discovery root while
  retaining the nested project as the decision and target scope.

## Should not trigger

- Create a README for this project.
- Invoke `$project-agent-instructions` directly without
  `maintain-project-specs`.
- Add a temporary rule for only this task.
- Update my global `~/.codex/AGENTS.md`.
- Create an `AGENTS.override.md` in every subdirectory.
