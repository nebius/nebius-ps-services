---
schema: agentic-sdlc/prompt-v1
prompt_id: {{PROMPT_ID}}
title: {{TITLE_JSON}}
created_at: {{CREATED_AT}}
---

<!-- markdownlint-disable MD025 -->

# {{TITLE}}

<!-- markdownlint-enable MD025 -->

## Ask

{{ASK}}

## Outcome

<!-- Required: describe what must be true when the SDLC run is complete. -->

## Context

<!-- Optional: add relevant repository facts, paths, or background. -->

## Constraints

- None.

## Acceptance criteria

- [ ] <!-- Required: add an observable, testable completion criterion. -->

## Verification

<!-- Required: name expected checks or ask Codex to derive them. -->

## Live Experiment Environment

<!--
Optional: describe a disposable or non-production environment, safe connection
steps, allowed actions, reset instructions, and evidence limits. Never add
credentials, tokens, private endpoints, customer data, or other secrets.
-->

## Non-goals

<!-- Optional: list intentionally excluded work. -->

## References

<!-- Optional: list repo-relative paths, tickets, or public URLs. -->

## Steering

<!--
Append clarifications, corrections, priorities, removals, or new requirements,
then repeat the same `$sdlc-start run <prompt>` command. Edits elsewhere in the
prompt are also treated as steering. Do not add secrets or customer data.
-->
