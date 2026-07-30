# Align

`align` is the repository consistency and post-change quality-gate skill. Use
it when a project needs a senior review-and-repair pass across implementation,
wiring, tests, CI, configuration, CLI behavior, help text, documentation, and
changelog entries.

## What It Does

- Inspects the actual project contract before editing.
- Synthesizes the active thread, relevant Agent Memory, and task-state context
  before deciding what to align.
- Separates requested or agent-touched changes from unrelated dirty files.
- Finds mismatches between code, tests, workflows, docs, and examples.
- Runs mandatory changed-scope code-review, lint/syntax, security, cross-code,
  and focused test/build validation lanes before completion. Its child
  `code-review` lane is report-only; `align` owns any safe remediation.
- Uses no-write/no-cache validation settings where available and removes exact
  task-created validation artifacts before reporting.
- Applies small, evidence-backed fixes instead of broad rewrites.
- Keeps behavior changes aligned with tests and user-facing documentation.
- Reports remaining uncertainty instead of guessing.

## Architecture

```text
User asks for alignment
  |
  v
Gather current thread, memory, and related state context
  |
  v
Map the relevant project contract
  |
  v
Separate active scope from unrelated dirty files
  |
  v
Compare code, tests, docs, workflows, and config
  |
  v
Patch confirmed inconsistencies
  |
  v
Run report-only code-review, lint/syntax, security,
cross-code, and focused test/build gates
  |
  v
Run focused validation and report residual risk
```

## Workflow

1. Consolidate the latest user request, current thread, relevant Agent Memory,
   and related task or workflow state.
2. Separate active scope from unrelated dirty files.
3. Inspect the relevant codebase surfaces before changing anything.
4. Establish the intended behavior from nearby evidence.
5. Prioritize real bugs, broken wiring, stale docs, missing tests, and unsafe
   assumptions.
6. Patch the smallest responsible surface.
7. Update tests, docs, examples, help text, and changelog entries when they are
   affected.
8. Validate with mandatory changed-scope gates: cross-code wiring checks,
   report-only `code-review`, `linter`, `apply-security`, and
   focused repository-native tests or builds. `align` owns safe remediation
   from the child review.
9. Broaden only when shared contracts, security-sensitive surfaces, or unclear
   dependency boundaries require it.

`apply-security` may be selected implicitly outside `align`; inside `align`, it
is mandatory. If it is not visible in the initial skills list because of
skill-list budget, installation, or discovery limits, `align` resolves and
reads the sibling or installed `apply-security/SKILL.md` before applying its
required-reference and safe-remediation rules.

## Core Concepts

- Evidence beats speculation.
- Memory and task state are decision inputs, not proof until verified against
  current repository or runtime evidence.
- Preserve intended behavior unless a bug or stale contract is proven.
- Preserve unrelated user changes; report them instead of folding them into
  scope silently.
- Prefer one canonical path over compatibility shims unless requested.
- Keep validation incremental and changed-scope first; do not default to a
  full-repo scan.
- Use safe-only remediation: fix low-risk confirmed issues and report risky
  security, public-contract, or architecture changes for explicit approval.
- Keep child `code-review` report-only; only `align` repairs its safe findings,
  and the child never calls `align`.
- Preserve caller-supplied finding IDs and classifications. Do not reclassify
  or repair caller-gated, owner-review, decision-required, or deferred findings
  without separate user authorization.
- Keep edits easy to review.

## Files

- `SKILL.md`: runtime alignment workflow and guardrails.
- `agents/openai.yaml`: UI metadata and invocation prompt.
- `references/quality-gate.md`: detailed changed-surface, wiring, review, and
  modularity checklist loaded only when mapping or validating a scope.
- `scripts/test_align_contract.py`: deterministic checks for the report-only
  child-review and parent-ledger boundaries.
