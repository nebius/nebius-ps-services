# Create Branch

Start independent local work from origin's freshly fetched default branch.
The skill updates the local default and switches to the new branch while
preserving the previous feature branch and its commits.

```text
$create-branch
$create-branch --branch feature/example
$create-branch --help
```

Invocation is explicit-only. Claude supports `/create-branch` and plugin
`/skills:create-branch`. Help is report-only. Python 3.10+ and Git with `switch`
and NUL-delimited worktree inspection are required; existing origin access is
reused. The helper has no third-party runtime dependency.

## Behavior

The generated format is `<repository-slug>-<eight-lowercase-alphanumeric-chars>`,
for example `example-repo-k7m2q9vx`. The prefix comes from origin's repository
basename, normalized to lowercase ASCII words separated by hyphens. Eight
independent base-36 characters provide 36^8 possibilities; collision checks,
not randomness alone, prevent reuse of observed local and remote names.
Generated collisions retry at most ten times. Explicit names remain literal;
namespace and case-only collisions are rejected for portable filesystem safety.

The helper uses native Git to discover the actual default (not necessarily
`main`), fetch that branch, safely update its local counterpart and create/switch
at the frozen commit. A missing local default receives origin tracking. Existing
default tracking is preserved; the new branch has no upstream until deliberately
published through another workflow.

Routine execution needs one remote-ref lookup, one targeted fetch and local
checks. Tests, installation, GitHub APIs and publication are not runtime steps.
Network and checkout latency dominate; no fixed timing guarantee is made.

## Preservation and failures

A clean primary checkout is required. Dirty files/submodules, pending Git
operations, occupied affected refs, unsupported environment overrides and
conflicting merge options stop execution. Unrelated worktree registrations,
including prunable detached ones, do not block. An applicable active workflow
must permit switching; the skill does not parse private owner stores or borrow
publication authority.

Assume-unchanged and skip-worktree index entries are rejected even when status
appears clean, including sparse checkouts using those flags. The helper never
clears them. Repository paths retain their exact whitespace during inspection.

The previous feature branch is preserved even if unmerged. Ahead/diverged local
defaults are never reset. Ignored files, normal hooks and signing policy remain
protected. A failed hook can leave the checkout changed: failure output records
the actual branch/commit and phase, and never rolls back or automatically retries.
Git errors omit raw remote diagnostics to avoid credential-bearing URLs.

The caller must keep one Git writer. State checks are not an exclusion lock;
remote refs can change after observation. Use the reported fetched commit as the
precise freshness boundary. An unavailable network never authorizes cached data.

## Files and verification

- `SKILL.md`: explicit invocation, workflow, guardrails and reporting.
- `scripts/create_branch.py`: deterministic Git and naming operations.
- `scripts/test_create_branch.py`: disposable local bare-origin regressions.
- `agents/openai.yaml`: Codex metadata and explicit invocation policy.
- `evals/trigger-prompts.csv`: positive and adjacent-workflow trigger cases.

Run the focused executable tests with:

```bash
python3 -B scripts/test_create_branch.py -q
```

The tests never contact GitHub or use the caller's Git configuration. They cover
histories, naming, dirty/ignored/submodule state, worktrees, hooks, network failure,
concurrency observations and postconditions. Static validation, disposable
packaging, actual checkout execution, installed bytes and fresh native discovery
are separate evidence boundaries; none implies another.

## Git references

- [Remote symbolic defaults](https://git-scm.com/docs/git-ls-remote)
- [Targeted fetching](https://git-scm.com/docs/git-fetch)
- [Branch-name validation](https://git-scm.com/docs/git-check-ref-format)
- [Transactional creation and switching](https://git-scm.com/docs/git-switch)
- [Fast-forward and ignored-file protection](https://git-scm.com/docs/git-merge)
