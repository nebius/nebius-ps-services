---
name: code-info
description: "Read-only project information gathering: summarize a local project folder or GitHub repository in a copy/paste-friendly report without changing project files. Use for a concise project description, documented feature count, hierarchical CLI command/subcommand counts through three levels, total or per-language LOC, package and dependency counts, famous-project size comparisons, repo size/link, tests, artifacts, coverage, or a GitHub repo that is not cloned locally."
---

# Code Info

## Help

For `$code-info --help` or `$code-info -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Generate a concise Markdown report for the project folder the user is working
in. This skill is inspection-only: using it is not permission to edit code,
update docs, run tests, generate coverage, build artifacts, install packages,
format files, or mutate the repository in any way.

Prefer the bundled script because these metrics are repetitive, deterministic,
and easy to make inconsistent by hand.

## Workflow

1. Resolve the target folder:
   - Use the current working directory unless the user names another path.
   - If the current directory is a service or package inside a monorepo, report
     metrics for that folder while still using Git metadata from the enclosing
     repository.
   - If the user names a GitHub repository and a local clone/path is available,
     prefer the local path.
   - If the GitHub repository is not cloned locally, use remote GitHub mode.
2. Run only the read-only reporting script from this skill folder.

   For local folders:

   ```bash
   python3 scripts/code_info.py --path <target-folder>
   ```

   For GitHub repositories that are not cloned locally:

   ```bash
   python3 scripts/code_info.py --github-repo <owner>/<repo>
   ```

   Add `--github-ref <branch-or-tag-or-sha>` when the user asks for a specific
   branch, tag, or commit.

   Use `--top <n>` when the user wants more or fewer detail rows. Summary
   totals are never truncated.
3. Return the script's Markdown output as the primary answer so the user can
   copy and paste it.
4. Mention only material limitations, such as missing coverage artifacts or
   best-effort CLI command detection.
5. Stop after reporting. If the user asks for code changes based on the report,
   treat that as a separate task outside `code-info`.

## Metrics Included

The script reports:

- A concise description from root package metadata or the primary README.
- Best-effort documented feature names and count from the primary README's
  `Features`, `Capabilities`, or `What It Does` section.
- Comparable code LOC, test LOC, documentation/configuration LOC, overall
  analyzed LOC, and per-language totals.
- Repo size for tracked files in scope, plus `.git` size when available.
- Repo link from `origin` when available.
- LOC per top-level package or component.
- Number of test files.
- Best-effort CLI command paths and counts at depths one, two, and three;
  package-manager scripts are reported separately.
- Project/workspace packages, package/module markers, and source module files.
- Unique direct runtime, development, optional, statically selected/resolved,
  and derived transitive dependency counts from supported Python, Node.js, Go,
  and Rust manifests, lockfiles, or Go module requirements.
- Approximate comparison with one or two pinned SQLite and Redis source trees
  measured using the same code-LOC method.
- Binary or build artifact sizes when detected.
- Test coverage from common coverage files when available.

## GitHub Repositories

Remote GitHub mode reads a repository archive without cloning it into the
project. It uses the GitHub REST repository tar archive endpoint.

Authentication behavior:

- First checks `GH_TOKEN` and `GITHUB_TOKEN`.
- If neither is set, falls back to `gh auth token` when GitHub CLI is
  installed and authenticated.
- Public repositories can be inspected without a token.
- Private repositories require a token with repository contents read access.
- Tokens are only sent in the request header; never print or persist token
  values in output, logs, task state, docs, or examples.

## Safety

- Do not edit, create, delete, move, format, generate, or stage files as part
  of this skill.
- Do not run tests, builds, package installs, formatters, generators,
  migrations, deployment commands, or coverage generation as part of this
  skill, even if those commands would produce more metrics.
- Treat this as read-only inspection. The bundled script reads files and prints
  to stdout. It may run read-only Git inspection commands such as
  `git rev-parse`, `git config`, `git ls-files`, and `git status --porcelain`.
  The script sets `GIT_OPTIONAL_LOCKS=0` for Git commands so status inspection
  does not refresh or lock the Git index as a side effect.
- Parse manifests and lockfiles as data only. Never import project modules,
  execute manifests, or invoke a package manager to fill missing metrics.
- Ignore symlinked files and directories so analysis cannot escape the selected
  project folder.
- For remote GitHub repositories, the script downloads a tar archive into a
  disposable temporary directory outside the project, analyzes it, and removes
  the temporary directory when the report is complete.
- If the script cannot collect a metric from existing files, report it as
  unavailable instead of creating artifacts or modifying the project.
- Redact repository links before sharing externally when the remote points at a
  private, internal, or non-public repository. The script defaults to
  `--repo-link auto`, which redacts obviously local or internal hosts; use
  `--repo-link redact` for public reports and `--repo-link show` only for
  internal reports.

## Output Rules

- Preserve the Markdown table formatting from the script.
- Keep caveats short and placed after the report.
- Do not invent unavailable metrics. Report `Not detected` or `Unavailable`
  when the evidence is missing.
- Treat feature, command, and dependency counts as static best-effort evidence,
  not proof of runtime behavior or dependency use.
- Treat famous-project comparisons as approximate size context only, never as
  measures of complexity, quality, effort, productivity, or value.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
