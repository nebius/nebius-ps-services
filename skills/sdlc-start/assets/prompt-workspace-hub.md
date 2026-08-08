# Agentic SDLC Prompt Workspace

## Start a new independent objective

Run the default build task (`Shift+Command+B` on macOS or
`Ctrl+Shift+B` on Windows/Linux), or select **Agentic SDLC: New Prompt** from
**Terminal > Run Task**. The task generates a fresh private prompt ID and opens
the new file. Do not clone an existing prompt.

Creating or saving a prompt never starts work. After editing `## Ask`, invoke:

```text
$sdlc-start run <prompt-path-or-unique-filename>
```

HTML comments are ignored by intent comparison. Keep every instruction in
`## Ask` or another visible heading.

## Continue or change existing work

- Active objective: edit the same prompt and repeat `run`; the edit is steering.
- Completed objective: edit the same prompt and repeat `run`; the current Ask is
  compiled as a linked fresh objective against current product truth.
- Independent objective: create a new prompt. An explicit `run` queues it when
  another objective is active; queued prompts run FIFO after the active run
  reaches a terminal state.

Use **Agentic SDLC: Prompt Queue** for metadata-only status and
**Agentic SDLC: Cancel Queued Prompt** to remove a waiting item.

Private prompt history is outside Git but is durable, not erasable by editing,
and not a secrets vault. Never put credentials, private endpoints, customer
data, or other sensitive material in prompts.
