# Trigger Prompts

## Explicit Invocation

These prompts should load the skill:

```text
$nosleep4mac
Use $nosleep4mac to ensure this Mac stays awake on AC power after I lock it.
$nosleep4mac repair its managed caffeinate LaunchAgent.
```

## Do Not Invoke Implicitly

These prompts must not load or run the skill without an explicit skill mention:

```text
Why does my Mac sleep?
Keep this terminal command awake for 20 minutes.
Change the display sleep timeout.
Disable battery sleep.
Keep a closed MacBook running.
Create a system-wide launch daemon.
```

## Static Runtime Check

Confirm that `agents/openai.yaml` keeps
`policy.allow_implicit_invocation: false`. Runtime activation is proven only
after observing a fresh Codex surface load an installed copy.
