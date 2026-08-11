# Prompt Session Intake Trigger Evals

## Routed material turn

Context: the current `UserPromptSubmit` hook receipt names the exact current
private event and acceptance token for a Task Implementer bound session.

Prompt: `Also require collision-safe five-character prompt references.`

Expected: use `prompt-session-intake`, classify the turn as material, refine it
losslessly, accept against the current canonical digest, merge through Task
Implementer, execute its normal run/resume path once, then consume the event.

## Routed conversation

Context: the current hook receipt names the exact current event for an Agentic
SDLC bound session.

Prompt: `Thanks, that explanation makes sense.`

Expected: classify as `conversation`, accept and consume without prompt
mutation or workflow execution.

## Unbound direct prompt

Prompt: `Please implement automatic prompt capture.`

Expected: do not use this skill. No exact current hook receipt exists.

## Manual file edit

Prompt: `I edited the managed prompt file.`

Expected: do not infer filesystem-triggered execution. Ask for or honor the
explicit workflow `run <prompt-ref-or-file>` action.

## Stop or older event

Context: an older staged event exists, but the current turn has no matching
hook receipt or is Stop-generated.

Expected: do not replay, accept, merge, or consume the older event.
