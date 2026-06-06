# Code Info

`code-info` summarizes local project folders or GitHub repositories in
copy/paste-ready Markdown. It is strictly read-only and does not change code or
project files.

## What It Does

- Counts source LOC per language.
- Counts source LOC per top-level component or package.
- Reports tracked repo size, `.git` size, and repository link when available.
- Counts test files, package/module markers, and source module files.
- Detects common CLI command and subcommand definitions.
- Reports build or binary artifact sizes when present.
- Reads common coverage artifacts without running tests.
- Reads a GitHub repository archive when the repo is not cloned locally.
- Stops after reporting; follow-up code changes are a separate task.

## Architecture

```text
Target folder
  |
  v
scripts/code_info.py
  |
  v
Markdown metrics report
```

## Workflow

1. Run `scripts/code_info.py` against the current project folder, or use
   `--github-repo <owner>/<repo>` when the repository is not cloned locally.
2. Review repo-link sensitivity before sharing the report outside the project.
3. Paste the generated Markdown report as the answer.
4. Do not edit, format, build, test, install, generate coverage, or stage files
   while using this skill.

## Core Concepts

- Metrics are best-effort and evidence-backed.
- The script is read-only and does not run tests, builds, installs, or network
  calls.
- Git inspections run with `GIT_OPTIONAL_LOCKS=0` to avoid refreshing or
  locking the Git index as a side effect.
- Remote GitHub analysis uses `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token`
  when available, then downloads a tar archive into a temporary directory that
  is removed after the report. Token values are never printed or stored.
- Missing metrics stay missing; the skill does not create artifacts to fill
  gaps.
- CLI command counts vary by framework; the report names the detection pattern
  it used.
- Coverage is reported only when a coverage artifact already exists.

## Files

- `SKILL.md`: runtime workflow and output rules.
- `scripts/code_info.py`: self-contained Markdown report generator.
- `agents/openai.yaml`: UI metadata.
