# Start SDLC

Start with `$sdlc-start <objective>` or `$sdlc-start` for the current unambiguous
plan. The same session continues its explicit binding; a new session uses
`$sdlc-start continue [project-folder]`. Help is read-only.

The coordinator persists normalized objectives and immutable execution bases,
keeps one accepted objective and its dependent feature queue, and routes existing
SDLC phases. Worktree owns Git transactions and fresh task worktrees. Root-owned
spec interpretation remains with maintain-project-specs. Global spec maintenance
is independent of workflow intake; no prompt capture is installed.

Prepared phases use the registered integration checkout. Promotion retains
unchanged-source checks and managed outer-worktree ownership. Post-promotion
documentation drift starts a corrective run. Remote publication still requires
its existing authorization and exact-commit checks.

See [SKILL.md](SKILL.md), [state schema](references/state-schema.md) and the
[shared execution contract](../worktree/references/workflow-contract.md).
