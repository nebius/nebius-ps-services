# Skills

This folder contains public, reusable Codex skills for common engineering
workflows. Each skill lives in its own folder and is discovered by the presence
of `SKILL.md`.

This root README is the concise index and install guide. Each skill folder also
has a local `README.md` that explains what the skill does, its architecture,
core concepts, workflow, and important files. `SKILL.md` remains the runtime
instruction file Codex loads when the skill is used.

For skill-specific release notes, see [CHANGELOG.md](CHANGELOG.md).

## Included Skills

- End-to-end project alignment: `align`
- Skill folder alignment and validation: `align-skill`
- Disposable Ubuntu project container setup: `attach-ubuntu`
- Commit and push the current feature branch: `commit-push`
- Branch-safe GitHub pull request creation with safe check repair: `create-pr`
- Read-only copy/paste-friendly local or GitHub repo code metrics: `code-info`
- Public-safe local Codex runtime configuration: `config-codex`
- GitHub Actions authoring and review: `github-workflows`
- Global context management for long Codex tasks: `global-context-management`
- Stack-aware `.gitignore` generation and cleanup: `gitignore`
- Helm chart hardening and validation: `helmchart`
- Shell, Markdown, and Python linting: `linter`
- Nebius cloud automation, quota, and MK8s GPU workflows: `nebius`
- Nebius cxcli component onboarding: `onboard-nebius-cxcli`
- Helm chart release publishing: `publish-helm`
- Container image release publishing: `publish-image`
- Application release publishing: `publish-release`
- Python project scaffolding and hardening: `python-project`
- Manual release-script generation: `release-generator`
- GitHub pull request review and merge-readiness repair: `review-pr`
- Bash and shell automation engineering: `shell-scripting`
- Terraform module and repo engineering: `terraform`

## Using Skills in Codex Chat

Use the exact skill name with a leading `$` in the Codex chat box, then add
the task you want. For example, use `$align` for this project,
`$shell-scripting` to harden a Bash script, or `$terraform` to scaffold or
review Terraform code.

### Prompt Examples

```text
$commit-push Commit all current changes on this feature branch, generate a commit message, push it to origin, and tell me whether the worktree is clean.

$create-pr Create a PR for the current local work, using a new prep branch if I am still on the default branch.

$create-pr Resolve conflicts for the current branch against main, open or reuse its PR, and return the PR URL.

$review-pr Review PR #110 against the base branch, fix safe issues on the branch, and tell me whether it is ready to merge.

$review-pr Review https://github.com/example-org/example-repo/pull/42, resolve straightforward conflicts against main if the branch is writable, and report remaining blockers.

$align-skill Review and standardize skills/foo against the canonical skill structure and official vendor docs.

$code-info Gather read-only project info from this folder or a GitHub repo with LOC by language and component, repo size, test files, CLI commands, modules, artifacts, and coverage.
```

You can also be more specific when needed:

```text
$create-pr Open or reuse the PR for this branch with title "Expose nccl-test 0.2.7 in the bundled catalog" and return the PR number and URL.

$create-pr Create conflict-free PRs for branches feature-a and feature-b against main, and tell me the merge order.

$review-pr Review this Helm chart PR, apply the relevant sibling skills, resolve straightforward conflicts if they exist, and rerun the focused validation.
```

These prompts should work when the skill is installed and the local environment
matches the task. For Git-backed flows such as `commit-push`, `create-pr`, and
`review-pr`, that means:

- the repository has an `origin` remote
- the branch state allows the requested operation

For GitHub CLI backed flows such as `create-pr` and `review-pr`, that also
means:

- `gh` is authenticated for the target repository

If those prerequisites are missing, the skill should stop and explain the
blocker instead of guessing.

Skill-bundled scripts may be executed for read-only inspection or validation
when they do not modify files, services, external systems, credentials, or
persistent state. Execute scripts that make changes only when the user
explicitly starts the request with `Run` or `Execute` (or equivalent);
otherwise read the script or report the command that would be used.

## Skill Details

### `align`

`align` is the end-to-end repair and consistency skill. Use it when a project
needs code, module wiring, tests, CI, CLI behavior, config, examples, help
output, README/design docs, workflows, and applicable project skills reviewed
together as a cautious senior code-review style alignment pass.

### `align-skill`

`align-skill` reviews and improves one or more Codex or Agent Skill folders.
Use it for named skills, local skill folders, multi-skill parent folders,
GitHub skill repositories, or GitHub tree URLs when `SKILL.md`, trigger
metadata, references, assets, scripts, safety guardrails, official vendor-doc
verification, canonical structure, and validation evidence need to be aligned.

### `attach-ubuntu`

`attach-ubuntu` launches or reuses a per-project `ubuntu:24.04` Docker
container, mounts the project at `/workdir`, prepares attached-container VS
Code defaults, and helps create a disposable Ubuntu environment for local
testing on macOS with Docker Desktop and the Dev Containers extension.

### `commit-push`

`commit-push` commits all current local changes on the active non-default
feature branch and pushes that branch to `origin`. It stages the complete
monorepo diff with `git add -A`, generates a commit message when needed, runs
lightweight Git validation, preserves normal hooks, and stops instead of
pulling, rebasing, merging, force-pushing, or opening a PR.

### `create-pr`

`create-pr` turns local work or named branches into GitHub pull requests
without leaving new work on the default branch. It can prepare conflict-free
PRs, repair safe branch-owned validation or GitHub check failures before
presenting the PR as handled, avoid duplicate PRs for the same head branch,
preserve one PR per branch, stage complete monorepo local work with
`git add -A` when committing current dirty changes, validate the staged diff,
reuse the current non-default branch without creating another branch, and
report readiness plus manual merge order.

### `code-info`

`code-info` summarizes a local project folder or a GitHub repository with
read-only, copy/paste-friendly Markdown metrics, including LOC per language,
LOC per top-level component, tracked repo size, repo link, test file counts,
CLI command definitions, package/module counts, build artifact sizes, and
already-available coverage artifacts. For not-yet-cloned GitHub repositories,
it reads a temporary archive using `GH_TOKEN`, `GITHUB_TOKEN`, or
`gh auth token` when needed. It does not edit, format, build, test, install,
generate coverage, or stage files.

### `config-codex`

`config-codex` bootstraps or aligns a user's local Codex runtime setup from
public-safe templates. Use it for `$CODEX_HOME` layout, global `AGENTS.md`
policy, `config.toml` features and MCP servers, hooks, task-state directories,
custom read-only agents, and validation without copying personal paths or
secrets into a public repository.

### `github-workflows`

`github-workflows` is the repository workflow skill for creating, reviewing,
and standardizing GitHub Actions. Use it for PR and merge CI, release
automation, container publication, merge-bot safety, permissions hardening, and
monorepo-friendly workflow structure.

### `global-context-management`

`global-context-management` keeps complex Codex sessions focused and
recoverable by using durable task-state files, limiting noisy parent-thread
exploration, delegating bounded read-only investigation when the user
explicitly authorizes delegation or enables a local hook delegation policy and
the runtime permits it, closing completed subagent threads after their results
are consolidated when close controls are available, and reviewing risk before
final answers. Its public skill files stay generic; local hooks, custom agent
config, and task-state files belong under `$CODEX_HOME`. The hook setup creates
task state lazily for complex prompts instead of scaffolding every session; the
file is meant to be read at task start, resume, or after compaction when prior
context may matter, then updated with concise decisions, validation status, and
next action.

### `gitignore`

`gitignore` creates or updates a project `.gitignore` with sensible macOS and
VS Code defaults, then extends it for the detected stack.

### `helmchart`

`helmchart` applies Helm chart best practices across metadata, values,
templates, schema, and validation.

### `linter`

`linter` runs a fix-first linting workflow for shell scripts, Markdown, and
Python. Use it when you want syntax checks, `shellcheck`, `markdownlint`, or
`ruff` cleanup applied conservatively.

### `nebius`

`nebius` is the cloud automation skill for Nebius SDK-based workflows,
including IAM bootstrap, object storage, VPC inspection, route analysis, quota
checks, observability, and MK8s GPU/operator decisions.

### `onboard-nebius-cxcli`

`onboard-nebius-cxcli` is the repo-specific onboarding skill for adding Nebius
Terraform-backed components into `nebius-cxcli`.

### `publish-helm`

`publish-helm` generates a Nebius OCI Helm chart publication flow with a
chart-local `CHANGELOG.md`, `publish-helm.sh`, and a tag-driven GitHub Actions
workflow.

### `publish-image`

`publish-image` generates the release assets for container images, including
`CHANGELOG.md`, `publish-image.sh`, and a tag-driven image publication
workflow.

### `publish-release`

`publish-release` generates the default application release flow for this skill
set: a `CHANGELOG.md`, `publish-release.sh`, and a tag-driven GitHub Release
workflow.

### `python-project`

`python-project` scaffolds and hardens Python repositories with reusable modern
defaults such as `pyproject.toml`, setuptools-scm, `src/` layout, Ruff, pytest,
Typer, and Pydantic.

### `release-generator`

`release-generator` is the manual-only fallback for projects that explicitly
want a local `release.sh` workflow and no CI release pipeline.

### `review-pr`

`review-pr` is the merge-readiness skill for pull requests. Use it to review a
PR by number, URL, or current branch against its base branch, inspect GitHub
checks and review state, fix safe issues when the branch can be updated safely,
resolve straightforward conflicts when possible, and report remaining blockers.

### `shell-scripting`

`shell-scripting` is the Bash engineering skill for creating, reviewing, and
hardening `.sh` automation.

### `terraform`

`terraform` generates and improves Terraform modules and infrastructure
repositories with reusable structure, state guidance, validation, security
controls, examples, and CI expectations.

## Skills Installer

`install-skills.sh` installs or updates skills into `~/.agents/skills` by
default. It accepts a local source directory or a supported GitHub URL, treats
only folders containing `SKILL.md` as installable skills, keeps reruns
idempotent with `rsync`, skips unmanaged or other-source-owned destinations,
removes stale same-source skills when they disappear from the selected source,
and can remove one installed skill by visible Codex skill name or folder name.
After each install it also lists destination skills that are not present in the
selected source, so renamed or intentionally removed skills are visible and can
be removed with `--remove-skill` when they are not same-source managed.

### Requirements

- `bash`
- `rsync`
- `git` for GitHub sources

### Usage

```bash
./install-skills.sh [source] [destination_dir]
./install-skills.sh --remove-skill <skill_name> [destination_dir]
./install-skills.sh --help
```

With no arguments, `./install-skills.sh` uses the directory containing the
script as the source and installs every sibling skill folder that contains
`SKILL.md` into the default Codex target, `~/.agents/skills`.

### Supported Sources

- Local directory path. The default source is the script directory, and a local
  source can be either a multi-skill folder or a single skill folder containing
  `SKILL.md`.
- GitHub repository URL:
  `https://github.com/<owner>/<repo>`
- GitHub tree URL:
  `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>`

### Examples

```bash
# Install all skills from this folder into the default destination
./install-skills.sh

# Install from an explicit local source directory
./install-skills.sh ~/test

# Install from a GitHub repository root
./install-skills.sh "https://github.com/openai/skills"

# Install from a nested GitHub skills folder
./install-skills.sh "https://github.com/openai/skills/tree/main/skills"

# Install one specific skill from a nested GitHub path
./install-skills.sh \
  "https://github.com/openai/skills/tree/main/skills/.curated/openai-docs"

# Install to a custom destination
./install-skills.sh \
  "https://github.com/openai/skills/tree/main/skills" \
  "~/custom-skills"

# Remove an installed skill by its visible Codex skill name
./install-skills.sh --remove-skill nebius

# Remove an installed skill by its folder name
./install-skills.sh --remove-skill vendor-nebius

# Remove from a custom destination
./install-skills.sh --remove-skill vendor-nebius "~/custom-skills"
```

### Notes

- If newly installed skills are not visible, run `Developer: Restart Extension
  Host` in VS Code.
- A valid skill folder must contain `SKILL.md`.
- Existing unmanaged folders in the destination are never overwritten.
- If an install prints `Skip (existing unmanaged directory): <skill_name>`,
  remove the destination copy and reinstall it from the current source:

  ```bash
  ./install-skills.sh --remove-skill <skill_name>
  ./install-skills.sh
  ```

- If a skill exists but belongs to another source, it is skipped.
- Skills previously installed from the same source are removed when they no
  longer exist in that source, so source-owned renames converge on reinstall.
- Other destination skills that are not present in the selected source are
  listed at the end with a `--remove-skill` hint.
- `--remove-skill` accepts either the exact `name:` from `SKILL.md` or the
  installed folder name.
- `--remove-skill <skill_name>` without an explicit destination removes from
  the default Codex skills target, `~/.agents/skills`.
- If you installed into a custom destination, pass that destination to
  `--remove-skill`.
- `--remove-skill` removes the destination skill folder and its local manifest
  entries.
- Reinstalling from a source that still contains a removed skill will add it
  back.
- Stale skill cleanup only applies to skills previously installed from the same
  source.
