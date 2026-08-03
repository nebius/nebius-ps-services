# Merge PR

`sdlc-merge-pr` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Merge only after explicit human instruction and final readiness verification.
The current remote PR head, local clean HEAD, promoted SHA, and review evidence
must all identify the same commit. The merge uses one exact authorized
`gh pr merge <pr-number-or-url> [--merge|--rebase|--squash]
--match-head-commit <sha>` command. The merge-queue form omits the strategy.
Other flags, implicit PR selection, and compound commands fail closed.
The PR and its short-lived authorization must also name the actual symbolic
`origin` default as their exact base branch and bind its recorded HEAD. A
changed default branch or HEAD blocks the merge.

## Main Boundaries

- Merge without explicit user request.
- Override branch protection.
- Force merge.
- Delete the branch or append another shell action.
- Merge failed UAT.
- Merge a PR head that differs from the promoted and reviewed SHA.

## Primary Inputs

- Explicit user merge request.
- PR URL or number.
- Desired merge method if specified.

## Output

- PR is merged or blocker is reported.
- Merge evidence is stored.
- SDLC run is marked complete when applicable.
