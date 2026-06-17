# Apply Security

`apply-security` is the reusable security review and remediation skill for
codebases that mix infrastructure, deployment, CI/CD, shell, and application
code. It is built for conservative security engineering: find real risk, rank
it clearly, and apply small patches only when they preserve intended behavior.

## Core Workflow

1. Select a mode: `scan`, `plan`, `patch`, `verify`, or `explain`.
2. Inspect repository evidence before asserting framework or cloud behavior.
3. Classify findings by severity, confidence, exploitability, and blast radius.
4. Patch only safe, local changes directly. Plan changes that can affect
   availability, auth, authz, crypto, data retention, or public exposure.
5. Verify with repository-native commands and report skipped checks.

## Important Files

- `SKILL.md`: runtime routing, workflow, guardrails, output contract, and
  learning-loop rule.
- `references/rulebook.md`: security review matrix and auto-fix policy.
- `references/examples.md`: before/after remediation examples for every
  supported area.
- `references/reporting-and-validation.md`: report formats, validation command
  selection, limitations, rollout guidance, and merge checklist.
- `agents/openai.yaml`: UI metadata and default prompt.

## Safety Model

The skill does not rotate credentials, rewrite history, delete resources,
disable features, change public APIs, change database schemas, replace
serialization protocols, change auth algorithms, or alter externally visible
routes without explicit approval. It uses explicit exceptions with owner,
reason, scope, and `reviewBy` date when risky behavior must remain.
