---
name: code-info
description: "Read-only project information gathering: summarize local project folders or GitHub repositories in a copy/paste-friendly code metrics report without changing code or files. Use when the user asks for basic project code information, repository statistics, LOC by language or component, repo size, repo link, test file counts, CLI command counts, module/package counts, build artifact sizes, available test coverage, or a GitHub repo that may not be cloned locally."
---

# Code Info

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

   Use `--top <n>` when the user wants more or fewer rows in component,
   language, package, or artifact tables.
3. Return the script's Markdown output as the primary answer so the user can
   copy and paste it.
4. Mention only material limitations, such as missing coverage artifacts or
   best-effort CLI command detection.
5. Stop after reporting. If the user asks for code changes based on the report,
   treat that as a separate task outside `code-info`.

## Metrics Included

The script reports:

- LOC per language.
- Repo size for tracked files in scope, plus `.git` size when available.
- Repo link from `origin` when available.
- LOC per top-level package or component.
- Number of test files.
- Best-effort number of CLI commands and subcommands.
- Number of package/module markers and source module files.
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

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.
